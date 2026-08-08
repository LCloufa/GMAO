from pathlib import Path
import shutil
import sys


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_stock_module.py")

BEGIN = "# BEGIN STOCK_MODULE"
END = "# END STOCK_MODULE"
ANCHOR = "# ==========================\n# Dashboard"

PATCH = r'''
# BEGIN STOCK_MODULE
from decimal import Decimal, InvalidOperation


def _stock_decimal(value, default="0"):
    try:
        raw = str(value if value not in (None, "") else default).strip().replace(",", ".")
        return Decimal(raw)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(str(default))


def _stock_article_rows(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT
            a.id,
            a.reference,
            a.designation,
            a.reference_fabricant,
            a.fabricant,
            a.unite,
            a.stock_min,
            a.stock_max,
            a.prix_unitaire,
            a.actif,
            a.notes,
            c.nom AS categorie,
            l.nom AS emplacement,
            COALESCE(SUM(m.quantite_delta), 0) AS stock_physique,
            COALESCE((
                SELECT SUM(r.quantite - r.quantite_consommee)
                FROM stock_reservations r
                WHERE r.article_id = a.id
                  AND r.statut = 'reserved'
            ), 0) AS reserve
        FROM stock_articles a
        LEFT JOIN stock_categories c ON c.id = a.categorie_id
        LEFT JOIN stock_locations l ON l.id = a.emplacement_id
        LEFT JOIN stock_movements m ON m.article_id = a.id
        GROUP BY
            a.id, a.reference, a.designation, a.reference_fabricant,
            a.fabricant, a.unite, a.stock_min, a.stock_max,
            a.prix_unitaire, a.actif, a.notes, c.nom, l.nom
        ORDER BY a.reference ASC
        """
    )

    rows = []
    for row in cursor.fetchall():
        physique = _stock_decimal(row[13])
        reserve = _stock_decimal(row[14])
        disponible = physique - reserve
        stock_min = _stock_decimal(row[6])
        prix = _stock_decimal(row[8])
        if physique <= 0:
            etat = "rupture"
        elif disponible <= stock_min:
            etat = "alerte"
        else:
            etat = "ok"
        rows.append({
            "id": row[0],
            "reference": row[1],
            "designation": row[2],
            "reference_fabricant": row[3],
            "fabricant": row[4],
            "unite": row[5],
            "stock_min": float(stock_min),
            "stock_max": float(_stock_decimal(row[7])) if row[7] is not None else None,
            "prix_unitaire": float(prix),
            "actif": bool(row[9]),
            "notes": row[10],
            "categorie": row[11] or "-",
            "emplacement": row[12] or "-",
            "stock_physique": float(physique),
            "reserve": float(reserve),
            "disponible": float(disponible),
            "valeur": float(physique * prix),
            "etat": etat,
        })
    return rows


def _stock_get_article_state(conn, article_id):
    for article in _stock_article_rows(conn):
        if int(article["id"]) == int(article_id):
            return article
    return None


def _stock_can_manage_intervention(conn, intervention_id):
    if str(session.get("role") or "").lower() == "admin":
        return True

    if str(session.get("role") or "").lower() != "technician":
        return False

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT 1
        FROM interventions i
        JOIN technicien_user_links tul ON tul.technicien_id = i.assigned_to
        WHERE i.id = ? AND tul.user_id = ?
        LIMIT 1
        """,
        (intervention_id, session.get("user_id")),
    )
    return cursor.fetchone() is not None


@app.route("/stock")
@login_required
@role_required("admin", "technician")
def stock_dashboard():
    section = request.args.get("section", "articles").strip().lower()
    if section not in {"articles", "mouvements", "inventaire", "fournisseurs", "alertes"}:
        section = "articles"

    recherche = request.args.get("q", "").strip().lower()
    conn = get_db_connection()
    cursor = conn.cursor()

    articles_all = _stock_article_rows(conn)
    articles = articles_all
    if recherche:
        articles = [
            a for a in articles_all
            if recherche in str(a["reference"]).lower()
            or recherche in str(a["designation"]).lower()
            or recherche in str(a["fabricant"] or "").lower()
            or recherche in str(a["categorie"] or "").lower()
            or recherche in str(a["emplacement"] or "").lower()
        ]

    cursor.execute("SELECT id, nom, description FROM stock_categories ORDER BY nom ASC")
    categories = cursor.fetchall()
    cursor.execute("SELECT id, code, nom, parent_id, description FROM stock_locations ORDER BY nom ASC")
    emplacements = cursor.fetchall()
    cursor.execute(
        "SELECT id, nom, contact, email, telephone, site_web, actif, notes FROM stock_suppliers ORDER BY nom ASC"
    )
    fournisseurs = cursor.fetchall()

    cursor.execute(
        """
        SELECT m.id, m.created_at, a.reference, a.designation,
               m.type_mouvement, m.quantite_delta, m.prix_unitaire,
               m.motif, COALESCE(u.username, '-'),
               m.intervention_id, COALESCE(i.title, '-')
        FROM stock_movements m
        JOIN stock_articles a ON a.id = m.article_id
        LEFT JOIN users u ON u.id = m.created_by_user_id
        LEFT JOIN interventions i ON i.id = m.intervention_id
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT 250
        """
    )
    mouvements = cursor.fetchall()

    cursor.execute(
        """
        SELECT i.id, i.title, i.scheduled_date, i.scheduled_time,
               e.nom, COALESCE(t.code, '-')
        FROM interventions i
        LEFT JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        WHERE i.status IN ('planned', 'in_progress')
        ORDER BY i.scheduled_date ASC, i.scheduled_time ASC
        """
    )
    interventions_stock = cursor.fetchall()

    alertes = [a for a in articles_all if a["actif"] and a["etat"] in {"alerte", "rupture"}]
    valeur_totale = round(sum(a["valeur"] for a in articles_all if a["actif"]), 2)
    nb_references = sum(1 for a in articles_all if a["actif"])
    nb_ruptures = sum(1 for a in alertes if a["etat"] == "rupture")

    conn.close()
    return render_template(
        "stock.html",
        section=section,
        recherche=recherche,
        articles=articles,
        articles_all=articles_all,
        categories=categories,
        emplacements=emplacements,
        fournisseurs=fournisseurs,
        mouvements=mouvements,
        interventions_stock=interventions_stock,
        alertes=alertes,
        stock_kpis={
            "references": nb_references,
            "valeur": valeur_totale,
            "alertes": len(alertes),
            "ruptures": nb_ruptures,
        },
    )


@app.route("/stock/categories/add", methods=["POST"])
@login_required
@admin_required
def stock_add_category():
    nom = request.form.get("nom", "").strip()
    description = request.form.get("description", "").strip() or None
    if not nom:
        return "Le nom de la catégorie est obligatoire.", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM stock_categories WHERE LOWER(nom) = LOWER(?)", (nom,))
    if cursor.fetchone():
        conn.close()
        return redirect("/stock?section=articles&error=category_exists")
    cursor.execute("INSERT INTO stock_categories (nom, description) VALUES (?, ?)", (nom, description))
    conn.commit()
    conn.close()
    return redirect("/stock?section=articles")


@app.route("/stock/locations/add", methods=["POST"])
@login_required
@admin_required
def stock_add_location():
    code = request.form.get("code", "").strip() or None
    nom = request.form.get("nom", "").strip()
    parent_id = request.form.get("parent_id") or None
    description = request.form.get("description", "").strip() or None
    if not nom:
        return "Le nom de l'emplacement est obligatoire.", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO stock_locations (code, nom, parent_id, description) VALUES (?, ?, ?, ?)",
        (code, nom, parent_id, description),
    )
    conn.commit()
    conn.close()
    return redirect("/stock?section=articles")


@app.route("/stock/fournisseurs/add", methods=["POST"])
@login_required
@admin_required
def stock_add_supplier():
    nom = request.form.get("nom", "").strip()
    if not nom:
        return "Le nom du fournisseur est obligatoire.", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO stock_suppliers
        (nom, contact, email, telephone, site_web, actif, notes)
        VALUES (?, ?, ?, ?, ?, TRUE, ?)
        """,
        (
            nom,
            request.form.get("contact", "").strip() or None,
            request.form.get("email", "").strip() or None,
            request.form.get("telephone", "").strip() or None,
            request.form.get("site_web", "").strip() or None,
            request.form.get("notes", "").strip() or None,
        ),
    )
    conn.commit()
    conn.close()
    return redirect("/stock?section=fournisseurs")


@app.route("/stock/articles/add", methods=["POST"])
@login_required
@admin_required
def stock_add_article():
    reference = request.form.get("reference", "").strip()
    designation = request.form.get("designation", "").strip()
    if not reference or not designation:
        return "Référence et désignation sont obligatoires.", 400

    stock_min = max(Decimal("0"), _stock_decimal(request.form.get("stock_min")))
    stock_max_raw = request.form.get("stock_max", "").strip()
    stock_max = _stock_decimal(stock_max_raw) if stock_max_raw else None
    prix = max(Decimal("0"), _stock_decimal(request.form.get("prix_unitaire")))
    stock_initial = max(Decimal("0"), _stock_decimal(request.form.get("stock_initial")))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM stock_articles WHERE LOWER(reference) = LOWER(?)", (reference,))
    if cursor.fetchone():
        conn.close()
        return redirect("/stock?section=articles&error=reference_exists")

    cursor.execute(
        """
        INSERT INTO stock_articles
        (reference, designation, reference_fabricant, fabricant, unite,
         categorie_id, emplacement_id, stock_min, stock_max,
         prix_unitaire, actif, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?, CURRENT_TIMESTAMP)
        """,
        (
            reference,
            designation,
            request.form.get("reference_fabricant", "").strip() or None,
            request.form.get("fabricant", "").strip() or None,
            request.form.get("unite", "pièce").strip() or "pièce",
            request.form.get("categorie_id") or None,
            request.form.get("emplacement_id") or None,
            stock_min,
            stock_max,
            prix,
            request.form.get("notes", "").strip() or None,
        ),
    )
    article_id = cursor.lastrowid
    if not article_id:
        cursor.execute("SELECT id FROM stock_articles WHERE reference = ?", (reference,))
        article_id = cursor.fetchone()[0]

    if stock_initial > 0:
        cursor.execute(
            """
            INSERT INTO stock_movements
            (article_id, type_mouvement, quantite_delta, prix_unitaire,
             motif, created_by_user_id, created_at)
            VALUES (?, 'inventaire', ?, ?, 'Stock initial', ?, CURRENT_TIMESTAMP)
            """,
            (article_id, stock_initial, prix, session.get("user_id")),
        )

    conn.commit()
    conn.close()
    return redirect(f"/stock/articles/{article_id}")


@app.route("/stock/articles/<int:article_id>")
@login_required
@role_required("admin", "technician")
def stock_article_detail(article_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    article = _stock_get_article_state(conn, article_id)
    if not article:
        conn.close()
        return "Article introuvable", 404

    cursor.execute("SELECT id, nom, description FROM stock_categories ORDER BY nom ASC")
    categories = cursor.fetchall()
    cursor.execute("SELECT id, code, nom, parent_id, description FROM stock_locations ORDER BY nom ASC")
    emplacements = cursor.fetchall()
    cursor.execute("SELECT id, nom FROM stock_suppliers WHERE actif = TRUE ORDER BY nom ASC")
    fournisseurs = cursor.fetchall()
    cursor.execute(
        """
        SELECT ass.id, s.nom, ass.reference_fournisseur, ass.prix,
               ass.delai_jours, ass.prefere
        FROM stock_article_suppliers ass
        JOIN stock_suppliers s ON s.id = ass.supplier_id
        WHERE ass.article_id = ?
        ORDER BY ass.prefere DESC, s.nom ASC
        """,
        (article_id,),
    )
    article_fournisseurs = cursor.fetchall()
    cursor.execute(
        """
        SELECT m.created_at, m.type_mouvement, m.quantite_delta,
               m.prix_unitaire, m.motif, COALESCE(u.username, '-'),
               m.intervention_id, COALESCE(i.title, '-')
        FROM stock_movements m
        LEFT JOIN users u ON u.id = m.created_by_user_id
        LEFT JOIN interventions i ON i.id = m.intervention_id
        WHERE m.article_id = ?
        ORDER BY m.created_at DESC, m.id DESC
        LIMIT 150
        """,
        (article_id,),
    )
    historique = cursor.fetchall()
    conn.close()
    return render_template(
        "stock_article.html",
        article=article,
        categories=categories,
        emplacements=emplacements,
        fournisseurs=fournisseurs,
        article_fournisseurs=article_fournisseurs,
        historique=historique,
    )


@app.route("/stock/articles/<int:article_id>/update", methods=["POST"])
@login_required
@admin_required
def stock_update_article(article_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE stock_articles
        SET designation = ?, reference_fabricant = ?, fabricant = ?, unite = ?,
            categorie_id = ?, emplacement_id = ?, stock_min = ?, stock_max = ?,
            prix_unitaire = ?, notes = ?
        WHERE id = ?
        """,
        (
            request.form.get("designation", "").strip(),
            request.form.get("reference_fabricant", "").strip() or None,
            request.form.get("fabricant", "").strip() or None,
            request.form.get("unite", "pièce").strip() or "pièce",
            request.form.get("categorie_id") or None,
            request.form.get("emplacement_id") or None,
            max(Decimal("0"), _stock_decimal(request.form.get("stock_min"))),
            _stock_decimal(request.form.get("stock_max")) if request.form.get("stock_max", "").strip() else None,
            max(Decimal("0"), _stock_decimal(request.form.get("prix_unitaire"))),
            request.form.get("notes", "").strip() or None,
            article_id,
        ),
    )
    conn.commit()
    conn.close()
    return redirect(f"/stock/articles/{article_id}")


@app.route("/stock/articles/<int:article_id>/toggle", methods=["POST"])
@login_required
@admin_required
def stock_toggle_article(article_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE stock_articles SET actif = NOT actif WHERE id = ?", (article_id,))
    conn.commit()
    conn.close()
    return redirect(f"/stock/articles/{article_id}")


@app.route("/stock/articles/<int:article_id>/supplier", methods=["POST"])
@login_required
@admin_required
def stock_link_supplier(article_id):
    supplier_id = request.form.get("supplier_id")
    if not supplier_id:
        return "Fournisseur obligatoire", 400
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id FROM stock_article_suppliers WHERE article_id = ? AND supplier_id = ?",
        (article_id, supplier_id),
    )
    existing = cursor.fetchone()
    params = (
        request.form.get("reference_fournisseur", "").strip() or None,
        _stock_decimal(request.form.get("prix")) if request.form.get("prix", "").strip() else None,
        int(request.form.get("delai_jours") or 0) or None,
        request.form.get("prefere") == "1",
    )
    if existing:
        cursor.execute(
            """
            UPDATE stock_article_suppliers
            SET reference_fournisseur = ?, prix = ?, delai_jours = ?, prefere = ?
            WHERE id = ?
            """,
            (*params, existing[0]),
        )
    else:
        cursor.execute(
            """
            INSERT INTO stock_article_suppliers
            (article_id, supplier_id, reference_fournisseur, prix, delai_jours, prefere)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (article_id, supplier_id, *params),
        )
    conn.commit()
    conn.close()
    return redirect(f"/stock/articles/{article_id}")


@app.route("/stock/mouvements/add", methods=["POST"])
@login_required
@role_required("admin", "technician")
def stock_add_movement():
    article_id = request.form.get("article_id")
    mouvement = request.form.get("type_mouvement", "").strip().lower()
    quantite = abs(_stock_decimal(request.form.get("quantite")))
    intervention_id = request.form.get("intervention_id") or None
    if not article_id or quantite <= 0:
        return "Article et quantité positive obligatoires.", 400

    role = str(session.get("role") or "").lower()
    if role == "admin":
        allowed = {"entree", "sortie", "correction", "retour"}
    else:
        allowed = {"sortie", "retour"}
    if mouvement not in allowed:
        return "Type de mouvement non autorisé.", 403

    if mouvement in {"sortie"}:
        delta = -quantite
    elif mouvement in {"entree", "retour"}:
        delta = quantite
    else:
        signe = request.form.get("signe", "+")
        delta = quantite if signe != "-" else -quantite

    conn = get_db_connection()
    article = _stock_get_article_state(conn, article_id)
    if not article:
        conn.close()
        return "Article introuvable", 404
    if delta < 0 and Decimal(str(article["stock_physique"])) + delta < 0:
        conn.close()
        return redirect("/stock?section=mouvements&error=insufficient")

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO stock_movements
        (article_id, type_mouvement, quantite_delta, prix_unitaire, motif,
         intervention_id, created_by_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            article_id,
            mouvement,
            delta,
            _stock_decimal(request.form.get("prix_unitaire")) if request.form.get("prix_unitaire", "").strip() else None,
            request.form.get("motif", "").strip() or None,
            intervention_id,
            session.get("user_id"),
        ),
    )
    conn.commit()
    conn.close()
    return redirect("/stock?section=mouvements")


@app.route("/stock/inventaire", methods=["POST"])
@login_required
@admin_required
def stock_inventory_adjust():
    article_id = request.form.get("article_id")
    compte = max(Decimal("0"), _stock_decimal(request.form.get("quantite_comptee")))
    conn = get_db_connection()
    article = _stock_get_article_state(conn, article_id)
    if not article:
        conn.close()
        return "Article introuvable", 404

    physique = Decimal(str(article["stock_physique"]))
    delta = compte - physique
    if delta != 0:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO stock_movements
            (article_id, type_mouvement, quantite_delta, prix_unitaire,
             motif, created_by_user_id, created_at)
            VALUES (?, 'inventaire', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                article_id,
                delta,
                Decimal(str(article["prix_unitaire"])),
                f"Inventaire : théorique {physique} / compté {compte}",
                session.get("user_id"),
            ),
        )
    conn.commit()
    conn.close()
    return redirect("/stock?section=inventaire")


@app.route("/stock/intervention/<int:intervention_id>")
@login_required
@role_required("admin", "technician")
def stock_intervention(intervention_id):
    conn = get_db_connection()
    if not _stock_can_manage_intervention(conn, intervention_id):
        conn.close()
        return "Accès refusé à cette intervention.", 403

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT i.id, i.title, i.status, i.scheduled_date, i.scheduled_time,
               e.nom, COALESCE(t.code, '-')
        FROM interventions i
        LEFT JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        WHERE i.id = ?
        """,
        (intervention_id,),
    )
    intervention = cursor.fetchone()
    if not intervention:
        conn.close()
        return "Intervention introuvable", 404

    articles = [a for a in _stock_article_rows(conn) if a["actif"]]
    cursor.execute(
        """
        SELECT r.id, a.reference, a.designation, r.quantite,
               r.quantite_consommee, r.statut, r.created_at
        FROM stock_reservations r
        JOIN stock_articles a ON a.id = r.article_id
        WHERE r.intervention_id = ?
        ORDER BY r.created_at DESC
        """,
        (intervention_id,),
    )
    reservations = cursor.fetchall()
    cursor.execute(
        """
        SELECT isi.created_at, a.reference, a.designation,
               isi.quantite_utilisee, isi.prix_unitaire, COALESCE(u.username, '-')
        FROM intervention_stock_items isi
        JOIN stock_articles a ON a.id = isi.article_id
        LEFT JOIN users u ON u.id = isi.created_by_user_id
        WHERE isi.intervention_id = ?
        ORDER BY isi.created_at DESC, isi.id DESC
        """,
        (intervention_id,),
    )
    consommations = cursor.fetchall()
    conn.close()
    return render_template(
        "stock_intervention.html",
        intervention=intervention,
        articles=articles,
        reservations=reservations,
        consommations=consommations,
    )


@app.route("/stock/intervention/<int:intervention_id>/reserve", methods=["POST"])
@login_required
@role_required("admin", "technician")
def stock_reserve_for_intervention(intervention_id):
    article_id = request.form.get("article_id")
    quantite = abs(_stock_decimal(request.form.get("quantite")))
    if not article_id or quantite <= 0:
        return "Article et quantité obligatoires", 400

    conn = get_db_connection()
    if not _stock_can_manage_intervention(conn, intervention_id):
        conn.close()
        return "Accès refusé", 403
    article = _stock_get_article_state(conn, article_id)
    if not article or Decimal(str(article["disponible"])) < quantite:
        conn.close()
        return redirect(f"/stock/intervention/{intervention_id}?error=insufficient")

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO stock_reservations
        (article_id, intervention_id, quantite, quantite_consommee,
         statut, created_by_user_id, created_at)
        VALUES (?, ?, ?, 0, 'reserved', ?, CURRENT_TIMESTAMP)
        """,
        (article_id, intervention_id, quantite, session.get("user_id")),
    )
    conn.commit()
    conn.close()
    return redirect(f"/stock/intervention/{intervention_id}")


@app.route("/stock/intervention/<int:intervention_id>/consume", methods=["POST"])
@login_required
@role_required("admin", "technician")
def stock_consume_for_intervention(intervention_id):
    article_id = request.form.get("article_id")
    quantite = abs(_stock_decimal(request.form.get("quantite")))
    if not article_id or quantite <= 0:
        return "Article et quantité obligatoires", 400

    conn = get_db_connection()
    if not _stock_can_manage_intervention(conn, intervention_id):
        conn.close()
        return "Accès refusé", 403
    article = _stock_get_article_state(conn, article_id)
    if not article or Decimal(str(article["stock_physique"])) < quantite:
        conn.close()
        return redirect(f"/stock/intervention/{intervention_id}?error=insufficient")

    cursor = conn.cursor()
    prix = Decimal(str(article["prix_unitaire"]))
    cursor.execute(
        """
        INSERT INTO stock_movements
        (article_id, type_mouvement, quantite_delta, prix_unitaire, motif,
         intervention_id, created_by_user_id, created_at)
        VALUES (?, 'consommation', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            article_id,
            -quantite,
            prix,
            request.form.get("motif", "").strip() or "Consommation intervention",
            intervention_id,
            session.get("user_id"),
        ),
    )
    mouvement_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO intervention_stock_items
        (intervention_id, article_id, mouvement_id, quantite_utilisee,
         prix_unitaire, created_by_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (intervention_id, article_id, mouvement_id, quantite, prix, session.get("user_id")),
    )

    remaining = quantite
    cursor.execute(
        """
        SELECT id, quantite, quantite_consommee
        FROM stock_reservations
        WHERE intervention_id = ? AND article_id = ? AND statut = 'reserved'
        ORDER BY created_at ASC, id ASC
        """,
        (intervention_id, article_id),
    )
    for reservation in cursor.fetchall():
        if remaining <= 0:
            break
        total = _stock_decimal(reservation[1])
        consumed = _stock_decimal(reservation[2])
        available_reserved = max(Decimal("0"), total - consumed)
        take = min(remaining, available_reserved)
        new_consumed = consumed + take
        new_status = "consumed" if new_consumed >= total else "reserved"
        cursor.execute(
            "UPDATE stock_reservations SET quantite_consommee = ?, statut = ? WHERE id = ?",
            (new_consumed, new_status, reservation[0]),
        )
        remaining -= take

    conn.commit()
    conn.close()
    return redirect(f"/stock/intervention/{intervention_id}")


@app.route("/stock/intervention/<int:intervention_id>/return", methods=["POST"])
@login_required
@role_required("admin", "technician")
def stock_return_from_intervention(intervention_id):
    article_id = request.form.get("article_id")
    quantite = abs(_stock_decimal(request.form.get("quantite")))
    if not article_id or quantite <= 0:
        return "Article et quantité obligatoires", 400

    conn = get_db_connection()
    if not _stock_can_manage_intervention(conn, intervention_id):
        conn.close()
        return "Accès refusé", 403
    article = _stock_get_article_state(conn, article_id)
    if not article:
        conn.close()
        return "Article introuvable", 404

    cursor = conn.cursor()
    prix = Decimal(str(article["prix_unitaire"]))
    cursor.execute(
        """
        INSERT INTO stock_movements
        (article_id, type_mouvement, quantite_delta, prix_unitaire, motif,
         intervention_id, created_by_user_id, created_at)
        VALUES (?, 'retour', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            article_id,
            quantite,
            prix,
            request.form.get("motif", "").strip() or "Retour intervention",
            intervention_id,
            session.get("user_id"),
        ),
    )
    mouvement_id = cursor.lastrowid
    cursor.execute(
        """
        INSERT INTO intervention_stock_items
        (intervention_id, article_id, mouvement_id, quantite_utilisee,
         prix_unitaire, created_by_user_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (intervention_id, article_id, mouvement_id, -quantite, prix, session.get("user_id")),
    )
    conn.commit()
    conn.close()
    return redirect(f"/stock/intervention/{intervention_id}")


@app.route("/stock/reservations/<int:reservation_id>/cancel", methods=["POST"])
@login_required
@role_required("admin", "technician")
def stock_cancel_reservation(reservation_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT intervention_id FROM stock_reservations WHERE id = ?", (reservation_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Réservation introuvable", 404
    intervention_id = row[0]
    if not _stock_can_manage_intervention(conn, intervention_id):
        conn.close()
        return "Accès refusé", 403
    cursor.execute(
        "UPDATE stock_reservations SET statut = 'cancelled' WHERE id = ? AND statut = 'reserved'",
        (reservation_id,),
    )
    conn.commit()
    conn.close()
    return redirect(f"/stock/intervention/{intervention_id}")
# END STOCK_MODULE

'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        print("Le module stock est déjà installé dans app.py.")
        return 0

    required_tokens = ("get_db_connection", "login_required", "role_required", "admin_required")
    missing = [token for token in required_tokens if token not in text]
    if missing:
        print("ERREUR : app.py ne contient pas les éléments attendus : " + ", ".join(missing))
        return 1

    anchor_index = text.find(ANCHOR)
    if anchor_index == -1:
        print(f"ERREUR : point d'insertion '{ANCHOR}' introuvable dans app.py.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    patched = text[:anchor_index] + PATCH + text[anchor_index:]
    APP_PATH.write_text(patched, encoding="utf-8")

    print("Module stock ajouté à app.py.")
    print("IMPORTANT : une migration Flask-Migrate/Alembic est nécessaire avant le démarrage.")
    print("Le script ne crée ni ne supprime aucune table lui-même.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
