from pathlib import Path
import re
import shutil
import sys


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_stock_supplier_fields.py")
BEGIN = "# BEGIN STOCK_SUPPLIER_FIELDS"
END = "# END STOCK_SUPPLIER_FIELDS"


SUPPLIER_ROUTE = r'''@app.route("/stock/fournisseurs/add", methods=["POST"])
@login_required
@admin_required
def stock_add_supplier():
    nom = request.form.get("nom", "").strip()
    if not nom:
        return "Le nom de la société est obligatoire.", 400

    siret_raw = request.form.get("siret", "").strip()
    siret = siret_raw.replace(" ", "") or None
    if siret and (not siret.isdigit() or len(siret) != 14):
        return redirect("/stock?section=fournisseurs&error=siret")

    conn = get_db_connection()
    cursor = conn.cursor()

    if siret:
        cursor.execute("SELECT id FROM stock_suppliers WHERE siret = ? LIMIT 1", (siret,))
        if cursor.fetchone():
            conn.close()
            return redirect("/stock?section=fournisseurs&error=siret_exists")

    cursor.execute(
        """
        INSERT INTO stock_suppliers
        (nom, adresse, siret, contact_nom, contact_prenom,
         telephone, email, site_web, actif, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, TRUE, ?)
        """,
        (
            nom,
            request.form.get("adresse", "").strip() or None,
            siret,
            request.form.get("contact_nom", "").strip() or None,
            request.form.get("contact_prenom", "").strip() or None,
            request.form.get("telephone", "").strip() or None,
            request.form.get("email", "").strip() or None,
            request.form.get("site_web", "").strip() or None,
            request.form.get("notes", "").strip() or None,
        ),
    )
    conn.commit()
    conn.close()
    return redirect("/stock?section=fournisseurs")
'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("Les champs fournisseur complets sont déjà installés dans app.py.")
        return 0

    if "# BEGIN STOCK_MODULE" not in text:
        print("ERREUR : le module stock doit être installé avant ce patch.")
        return 1

    old_query = (
        '"SELECT id, nom, contact, email, telephone, site_web, actif, notes '
        'FROM stock_suppliers ORDER BY nom ASC"'
    )
    new_query = (
        '"SELECT id, nom, adresse, siret, contact_nom, contact_prenom, telephone, '
        'email, site_web, notes, actif FROM stock_suppliers ORDER BY nom ASC"'
    )
    if old_query not in text:
        print("ERREUR : requête fournisseur attendue introuvable dans app.py.")
        return 1

    supplier_pattern = re.compile(
        r'@app\.route\("/stock/fournisseurs/add", methods=\["POST"\]\)\s*\n'
        r'@login_required\s*\n@admin_required\s*\n'
        r'def stock_add_supplier\(\):.*?'
        r'(?=\n\n@app\.route\("/stock/articles/add")',
        re.DOTALL,
    )
    if not supplier_pattern.search(text):
        print("ERREUR : route de création fournisseur introuvable dans app.py.")
        return 1

    render_anchor = '''    nb_ruptures = sum(1 for a in alertes if a["etat"] == "rupture")

    conn.close()
    return render_template(
'''
    render_replacement = '''    nb_ruptures = sum(1 for a in alertes if a["etat"] == "rupture")

    if section == "fournisseurs":
        conn.close()
        return render_template(
            "stock_fournisseurs.html",
            fournisseurs=fournisseurs,
            stock_kpis={
                "references": nb_references,
                "valeur": valeur_totale,
                "alertes": len(alertes),
                "ruptures": nb_ruptures,
            },
        )

    conn.close()
    return render_template(
'''
    if render_anchor not in text:
        print("ERREUR : point d'insertion de la page fournisseurs introuvable dans app.py.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    text = text.replace(old_query, new_query, 1)
    text = supplier_pattern.sub(SUPPLIER_ROUTE.rstrip(), text, count=1)
    text = text.replace(render_anchor, render_replacement, 1)

    marker = f"\n{BEGIN}\n# Champs fournisseur : adresse, SIRET, nom/prénom contact.\n{END}\n"
    stock_end = text.find("# END STOCK_MODULE")
    if stock_end != -1:
        stock_end += len("# END STOCK_MODULE")
        text = text[:stock_end] + marker + text[stock_end:]
    else:
        text += marker

    APP_PATH.write_text(text, encoding="utf-8")
    print("Champs fournisseur complets ajoutés à app.py.")
    print("Le SIRET est validé sur 14 chiffres et contrôlé en doublon.")
    print("Aucune donnée existante n'est supprimée par ce patch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
