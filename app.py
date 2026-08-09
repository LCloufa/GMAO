from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_file
from openpyxl import load_workbook
from tempfile import NamedTemporaryFile
from dotenv import load_dotenv
from flask_migrate import Migrate
from models import db
from database_compat import get_db_connection
from intervention_report_pdf import create_intervention_report_pdf
from declaration_pdf import create_declaration_pdf
from excel_export import create_gmao_excel_export
from maintenance_metrics import calculate_availability_metrics
import os
from werkzeug.utils import secure_filename
import csv
from flask import Response
load_dotenv()

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL est absent du fichier .env")

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
migrate = Migrate(app, db)

app.secret_key = os.getenv("SECRET_KEY")
ADMIN_ACCESS_KEY = os.getenv("ADMIN_ACCESS_KEY")
OPERATOR_ACCESS_KEY = os.getenv("OPERATOR_ACCESS_KEY")
TECH_ACCESS_KEY = os.getenv("TECH_ACCESS_KEY")

if not all((app.secret_key, ADMIN_ACCESS_KEY, OPERATOR_ACCESS_KEY, TECH_ACCESS_KEY)):
    raise RuntimeError(
        "SECRET_KEY, ADMIN_ACCESS_KEY, OPERATOR_ACCESS_KEY et TECH_ACCESS_KEY "
        "doivent être définies dans .env"
    )

def ensure_upload_dirs():
    os.makedirs("static/uploads/pannes", exist_ok=True)
    os.makedirs("static/uploads/photos", exist_ok=True)
    os.makedirs("static/uploads/documents", exist_ok=True)
    
# ==========================
# Protection routes
# ==========================

from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if session.get("role") != "admin":
            return "Accès refusé"
        return f(*args, **kwargs)
    return decorated_function



def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            if "user_id" not in session:
                return redirect("/login")
            if session.get("role") not in roles:
                return "Accès refusé", 403
            return f(*args, **kwargs)
        return wrapped
    return decorator



# BEGIN USER_ROLE_SESSION_SYNC
@app.before_request
def sync_authenticated_user_role():
    """Resynchronise le rôle de la session avec PostgreSQL.

    Ainsi, une promotion ou rétrogradation décidée par un administrateur prend
    effet dès la requête suivante, y compris pour un utilisateur déjà connecté.
    """
    if "user_id" not in session or request.path.startswith("/static/"):
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE id = ?", (session.get("user_id"),))
    row = cursor.fetchone()
    conn.close()

    if not row:
        session.clear()
        return redirect("/login")

    database_role = str(row[0] or "").strip().lower()
    if database_role and session.get("role") != database_role:
        session["role"] = database_role

    return None
# END USER_ROLE_SESSION_SYNC

# BEGIN OPERATOR_ACCESS_GUARD
@app.before_request
def restrict_operator_access():
    """Limite un opérateur au dashboard et aux déclarations de panne.

    Les administrateurs et techniciens conservent leurs accès actuels.
    Les routes de traitement d'une déclaration restent protégées par leurs
    décorateurs role_required existants.
    """
    role = str(session.get("role") or "").strip().lower()
    if role != "operator":
        return None

    path = request.path or "/"

    allowed = (
        path == "/"
        or path.startswith("/declarations")
        or path.startswith("/static/")
        or path in {"/login", "/logout"}
    )

    if allowed:
        return None

    return "Accès refusé : le profil opérateur est limité au tableau de bord et aux déclarations de panne.", 403
# END OPERATOR_ACCESS_GUARD


# BEGIN TECHNICIAN_ONBOARDING
@app.before_request
def require_technician_profile():
    """Force un technicien à compléter son profil avant d'accéder à la GMAO."""
    role = str(session.get("role") or "").strip().lower()
    if role != "technician":
        return None

    path = request.path or "/"
    if path.startswith("/static/") or path in {"/logout", "/mon-profil-technicien"}:
        return None

    user_id = session.get("user_id")
    if not user_id:
        return redirect("/login")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT technicien_id FROM technicien_user_links WHERE user_id = ? LIMIT 1",
        (user_id,),
    )
    profile = cursor.fetchone()
    conn.close()

    if profile:
        return None

    return redirect("/mon-profil-technicien")


@app.route("/mon-profil-technicien", methods=["GET", "POST"])
@login_required
def technicien_onboarding():
    role = str(session.get("role") or "").strip().lower()
    if role != "technician":
        return redirect("/")

    user_id = session.get("user_id")

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT technicien_id FROM technicien_user_links WHERE user_id = ? LIMIT 1",
        (user_id,),
    )
    existing = cursor.fetchone()

    if existing:
        conn.close()
        return redirect("/")

    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        prenom = request.form.get("prenom", "").strip()
        code = request.form.get("code", "").strip()
        specialite = request.form.get("specialite", "").strip()
        statut = request.form.get("statut", "Actif").strip()

        if not nom or not prenom or not code:
            conn.close()
            return "Nom, prénom et code sont obligatoires.", 400

        if statut not in {"Actif", "Inactif"}:
            conn.close()
            return "Statut invalide.", 400

        cursor.execute(
            """
            INSERT INTO techniciens
            (nom, prenom, code, specialite, statut)
            VALUES (?, ?, ?, ?, ?)
            """,
            (nom, prenom, code, specialite or None, statut),
        )
        technicien_id = cursor.lastrowid

        if not technicien_id:
            conn.rollback()
            conn.close()
            return "Impossible de créer le profil technicien.", 500

        cursor.execute(
            """
            INSERT INTO technicien_user_links (user_id, technicien_id)
            VALUES (?, ?)
            """,
            (user_id, technicien_id),
        )

        conn.commit()
        conn.close()
        return redirect("/")

    conn.close()
    return render_template("technicien_onboarding.html")
# END TECHNICIAN_ONBOARDING

RYTHME_OPTIONS = ["1x8", "2x8", "3x8", "24/7"]
RYTHME_MINUTES_PER_DAY = {
    "1x8": 8 * 60,
    "2x8": 16 * 60,
    "3x8": 24 * 60,
    "24/7": 24 * 60,
}

def normalize_rythme(value):
    if value in RYTHME_OPTIONS:
        return value
    return "1x8"

def compute_disponibilite(rate_minutes_per_day, equipment_count, downtime_minutes, days_window=30):
    total_capacity = max(1, int(rate_minutes_per_day or 0) * max(1, int(equipment_count or 0)) * days_window)
    ratio = 100 - ((float(downtime_minutes or 0) / total_capacity) * 100)
    return round(max(0, min(100, ratio)), 1)

# ==========================
# Base de données
# ==========================

def init_db():
    """Crée les tables manquantes dans PostgreSQL sans supprimer les données."""
    with app.app_context():
        db.create_all()


def sync_equipement_statut(conn, equipement_id):
    """Synchronise automatiquement le statut équipement selon pannes/interventions actives."""
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM declarations_panne
            WHERE equipment_id = ?
              AND status IN ('pending', 'in_progress')
        )
        """,
        (equipement_id,),
    )
    has_active_declaration = bool(cursor.fetchone()[0])

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM interventions
            WHERE equipment_id = ?
              AND status IN ('planned', 'in_progress')
        )
        """,
        (equipement_id,),
    )
    has_active_intervention = bool(cursor.fetchone()[0])

    if has_active_intervention:
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM interventions
                WHERE equipment_id = ?
                  AND status = 'in_progress'
            )
            """,
            (equipement_id,),
        )
        has_in_progress_intervention = bool(cursor.fetchone()[0])
        next_statut = "Maintenance en cours" if has_in_progress_intervention else "Planifiée"
    elif has_active_declaration:
        next_statut = "Problème"
    else:
        next_statut = "Opérationnel"

    cursor.execute(
        "UPDATE equipements SET statut = ? WHERE id = ?",
        (next_statut, equipement_id),
    )
# ==========================
# Inscription/Connexion/Déconnexion
# ==========================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])
        access_key = request.form.get("access_key")

        role = "operator"  # par défaut : opérateur (logique GMAO)

        # Si clé admin valide
        if access_key == ADMIN_ACCESS_KEY:
            role = "admin"
        elif access_key == TECH_ACCESS_KEY:
            role = "technician"
        elif access_key == OPERATOR_ACCESS_KEY:
            role = "operator"

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                (username, password, role)
            )
            conn.commit()
        except:
            return "Utilisateur déjà existant"

        conn.close()
        return redirect("/login")

    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db_connection()
        conn.row_factory = dict
        cursor = conn.cursor()

        cursor.execute("SELECT * FROM users WHERE username=?", (username,))
        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            return redirect("/")
        else:
            return "Identifiants incorrects"

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ==========================
# Gestion compte
# ==========================

@app.route("/users")
@admin_required
def users():

    if session["role"] != "admin":
        return "Accès refusé"


    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, username, role FROM users")
    users = cursor.fetchall()

    conn.close()
    return render_template("users.html", users=users)

@app.route("/debug-users")
@admin_required
def debug_users():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, username, role FROM users")
    users = cur.fetchall()
    conn.close()
    return str(users)

# BEGIN USER_ROLE_MANAGEMENT
@app.route("/users/<int:id>/role", methods=["POST"])
@admin_required
def update_user_role(id):
    """Permet à un admin de basculer un compte non-admin entre opérateur et technicien."""
    new_role = str(request.form.get("role") or "").strip().lower()

    # L'interface ne propose que ces deux valeurs, et le backend les impose aussi.
    if new_role not in ("operator", "technician"):
        return "Rôle invalide. Seuls Opérateur et Technicien sont autorisés.", 400

    # Un administrateur ne peut pas modifier son propre rôle par cette fonction.
    if id == session.get("user_id"):
        return "Le rôle de votre propre compte administrateur est protégé.", 403

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, role FROM users WHERE id = ?", (id,))
    user = cursor.fetchone()

    if not user:
        conn.close()
        return "Utilisateur introuvable", 404

    current_role = str(user[2] or "").strip().lower()

    # Aucun compte administrateur ne peut être rétrogradé depuis cette interface.
    if current_role == "admin":
        conn.close()
        return "Le rôle d'un administrateur est protégé.", 403

    cursor.execute(
        "UPDATE users SET role = ? WHERE id = ?",
        (new_role, id),
    )
    conn.commit()
    conn.close()

    return redirect("/users")
# END USER_ROLE_MANAGEMENT

# ==========================
# Suppression compte
# ==========================

@app.route("/users/delete/<int:id>", methods=["POST"])
@admin_required
def delete_user(id):
    # Empêche de se supprimer soi-même
    if session.get("user_id") == id:
        return "Vous ne pouvez pas supprimer votre propre compte"

    conn = get_db_connection()
    cursor = conn.cursor()

    # Compter les admins
    cursor.execute("SELECT COUNT(*) FROM users WHERE role='admin'")
    admin_count = cursor.fetchone()[0]

    # Vérifier si on supprime un admin
    cursor.execute("SELECT role FROM users WHERE id=?", (id,))
    user = cursor.fetchone()

    if user and user[0] == "admin" and admin_count <= 1:
        conn.close()
        return "Impossible de supprimer le dernier administrateur"

    cursor.execute("DELETE FROM users WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    return redirect("/users")


# BEGIN SELECTIVE_DATABASE_RESET
@app.route("/admin/reset-data", methods=["POST"])
@login_required
@admin_required
def reset_selected_data():
    """Réinitialise uniquement la catégorie choisie par un administrateur.

    Les suppressions sont réalisées dans un ordre compatible avec les clés
    étrangères PostgreSQL. Les utilisateurs, clients et techniciens ne sont
    jamais supprimés par cette fonction.
    """
    target = str(request.form.get("reset_target") or "").strip().lower()
    allowed_targets = {"equipements", "interventions", "rapports", "declarations"}

    if target not in allowed_targets:
        return "Choix de réinitialisation invalide.", 400

    conn = get_db_connection()
    cursor = conn.cursor()
    affected_equipment_ids = set()

    try:
        if target == "rapports":
            cursor.execute(
                """
                SELECT DISTINCT i.equipment_id
                FROM rapports_intervention r
                JOIN interventions i ON i.id = r.intervention_id
                WHERE i.equipment_id IS NOT NULL
                """
            )
            affected_equipment_ids = {int(row[0]) for row in cursor.fetchall() if row[0] is not None}

            # Un rapport clôt une intervention dans la logique GMAO. Si tous
            # les rapports sont effacés, les interventions concernées sont
            # rouvertes afin de ne pas conserver un état "completed" sans
            # document de clôture.
            cursor.execute(
                """
                UPDATE interventions
                SET status = 'in_progress', completion_date = NULL
                WHERE id IN (
                    SELECT DISTINCT intervention_id
                    FROM rapports_intervention
                )
                """
            )
            cursor.execute("DELETE FROM rapports_intervention")

        elif target == "declarations":
            cursor.execute(
                "SELECT DISTINCT equipment_id FROM declarations_panne WHERE equipment_id IS NOT NULL"
            )
            affected_equipment_ids = {int(row[0]) for row in cursor.fetchall() if row[0] is not None}

            cursor.execute("DELETE FROM declaration_photos")
            cursor.execute("DELETE FROM declarations_panne")

        elif target == "interventions":
            cursor.execute(
                "SELECT DISTINCT equipment_id FROM interventions WHERE equipment_id IS NOT NULL"
            )
            affected_equipment_ids = {int(row[0]) for row in cursor.fetchall() if row[0] is not None}

            # Les déclarations restent conservées. Elles sont simplement
            # détachées de l'intervention supprimée et remises en attente si
            # elles avaient été avancées par cette intervention.
            cursor.execute(
                """
                UPDATE declarations_panne
                SET intervention_id = NULL,
                    status = CASE
                        WHEN status IN ('in_progress', 'resolved') THEN 'pending'
                        ELSE status
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE intervention_id IS NOT NULL
                """
            )

            cursor.execute("DELETE FROM rapports_intervention")
            cursor.execute("DELETE FROM interventions")

        elif target == "equipements":
            # Une intervention ou une déclaration ne peut pas exister sans
            # équipement dans le schéma actuel. La remise à zéro des
            # équipements efface donc aussi leurs données métiers liées.
            cursor.execute("DELETE FROM declaration_photos")
            cursor.execute("DELETE FROM rapports_intervention")
            cursor.execute("DELETE FROM declarations_panne")
            cursor.execute("DELETE FROM interventions")
            cursor.execute("DELETE FROM equipement_documents")
            cursor.execute("DELETE FROM equipements")

        if target != "equipements":
            for equipment_id in affected_equipment_ids:
                sync_equipement_statut(conn, equipment_id)

        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.close()
        print(f"Erreur réinitialisation {target}: {exc}")
        return "La réinitialisation a échoué. Aucune modification n'a été validée.", 500

    conn.close()
    return redirect(f"/?reset_done={target}")
# END SELECTIVE_DATABASE_RESET


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
        "SELECT id, nom, adresse, siret, contact_nom, contact_prenom, telephone, email, site_web, notes, actif FROM stock_suppliers ORDER BY nom ASC"
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

    # BEGIN STOCK_MOVEMENT_AUTO_PRICE
    # Le prix unitaire du mouvement reprend par défaut celui de la fiche article.
    # Une valeur explicitement saisie reste possible et le total est dérivable
    # par abs(quantite_delta) * prix_unitaire.
    prix_saisi = request.form.get("prix_unitaire", "").strip()
    if prix_saisi:
        movement_unit_price = max(Decimal("0"), _stock_decimal(prix_saisi))
    else:
        movement_unit_price = max(
            Decimal("0"),
            _stock_decimal(article.get("prix_unitaire", 0)),
        )
    # END STOCK_MOVEMENT_AUTO_PRICE

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
            movement_unit_price,
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
# BEGIN STOCK_SUPPLIER_FIELDS
# Champs fournisseur : adresse, SIRET, nom/prénom contact.
# END STOCK_SUPPLIER_FIELDS


# ==========================
# Dashboard
# ==========================
from datetime import datetime, date, time, timedelta

WORK_SLOTS = [
    ("08:00", "12:00"),
    ("13:00", "17:00"),
]

def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))

def split_into_work_segments(start_dt: datetime, duration_minutes: int):
    """
    Découpe une intervention en segments qui respectent :
    08:00-12:00 et 13:00-17:00 (lun-ven)
    Retourne une liste de tuples (seg_start_dt, seg_end_dt)
    """
    remaining = duration_minutes
    cur = start_dt
    segments = []

    # Si pas d'heure fournie: on force à 08:00
    if cur.time() == time(0, 0):
        cur = cur.replace(hour=8, minute=0)

    while remaining > 0:
        # Weekend -> lundi suivant 08:00
        while cur.weekday() >= 5:  # 5=Sam, 6=Dim
            cur = datetime.combine((cur.date() + timedelta(days=1)), time(8, 0))

        day = cur.date()

        # Trouver le prochain slot valide dans la journée
        placed = False
        for slot_start, slot_end in WORK_SLOTS:
            s = datetime.combine(day, _parse_hhmm(slot_start))
            e = datetime.combine(day, _parse_hhmm(slot_end))

            # Si on est après la fin du slot, on passe au suivant
            if cur >= e:
                continue

            # Si on est avant le slot, on se cale au début
            seg_start = max(cur, s)

            # minutes dispo dans ce slot
            available = int((e - seg_start).total_seconds() // 60)
            if available <= 0:
                continue

            use = min(remaining, available)
            seg_end = seg_start + timedelta(minutes=use)

            segments.append((seg_start, seg_end))
            remaining -= use
            cur = seg_end
            placed = True
            break

        # Si aucun slot restant aujourd’hui -> jour suivant 08:00
        if not placed:
            cur = datetime.combine(day + timedelta(days=1), time(8, 0))

    return segments
    
@app.route("/")
@login_required
def dashboard():
    conn = get_db_connection()
    cursor = conn.cursor()

    selected_client = request.args.get("client")

    # ==========================
    # KPI
    # ==========================

    if selected_client:
        cursor.execute("""
            SELECT COUNT(*)
            FROM equipements
            WHERE client_id = ?
        """, (selected_client,))
        nb_equipements = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM interventions i
            JOIN equipements e ON i.equipment_id = e.id
            WHERE e.client_id = ?
            AND i.status = 'in_progress'
        """, (selected_client,))
        in_progress = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*)
            FROM interventions i
            JOIN equipements e ON i.equipment_id = e.id
            WHERE e.client_id = ?
            AND i.status = 'planned'
        """, (selected_client,))
        planned = cursor.fetchone()[0]

    else:
        cursor.execute("SELECT COUNT(*) FROM equipements")
        nb_equipements = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM interventions WHERE status='in_progress'")
        in_progress = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM interventions WHERE status='planned'")
        planned = cursor.fetchone()[0]

    # ==========================
    # LISTE CLIENTS (pour filtre)
    # ==========================

    cursor.execute("SELECT id, nom FROM clients")
    clients = cursor.fetchall()

    # ==========================
    # INTERVENTIONS (CALENDRIER)
    # ==========================

    if selected_client:
        cursor.execute("""
            SELECT i.title,
                   i.scheduled_date,
                   i.scheduled_time,
                   i.estimated_duration,
                   i.priority,
                   e.nom,
                   t.code,
                   c.nom,
                   i.description,
                   i.id
            FROM interventions i
            LEFT JOIN equipements e ON i.equipment_id = e.id
            LEFT JOIN techniciens t ON i.assigned_to = t.id
            LEFT JOIN clients c ON e.client_id = c.id
            WHERE c.id = ?
            AND i.status IN ('planned','in_progress')
            ORDER BY i.scheduled_date ASC, i.scheduled_time ASC
        """, (selected_client,))
    else:
        cursor.execute("""
            SELECT i.title,
                   i.scheduled_date,
                   i.scheduled_time,
                   i.estimated_duration,
                   i.priority,
                   e.nom,
                   t.code,
                   c.nom,
                   i.description,
                   i.id
            FROM interventions i
            LEFT JOIN equipements e ON i.equipment_id = e.id
            LEFT JOIN techniciens t ON i.assigned_to = t.id
            LEFT JOIN clients c ON e.client_id = c.id
            WHERE i.status IN ('planned','in_progress')
            ORDER BY i.scheduled_date ASC, i.scheduled_time ASC
        """)
    interventions = cursor.fetchall()
    # ===== SEGMENTATION pour le planning =====
    segmented = []
    for i in interventions:
        title = i[0]
        scheduled_date = i[1]
        scheduled_time = i[2] or "08:00"
        duration = i[3] or 60
        priority = i[4]
        equipement = i[5]
        technicien = i[6]
        client = i[7]
        description = i[8]
        orig_id = i[9]

        start_dt = datetime.fromisoformat(f"{scheduled_date}T{scheduled_time}")
        parts = split_into_work_segments(start_dt, int(duration))

        for idx, (pstart, pend) in enumerate(parts):
            segmented.append([
                title,
                pstart.isoformat(timespec="minutes"),
                pend.isoformat(timespec="minutes"),
                priority,
                equipement,
                technicien,
                client,
                description,
                orig_id,   # id original
                idx        # index segment
            ])
    
    # ==========================
    # ETAT EQUIPEMENTS (ta logique actuelle)
    # ==========================

    from datetime import timedelta

    query = """
    SELECT 
        e.id,
        e.nom,
        c.nom,
        CASE
        WHEN EXISTS (
            SELECT 1 FROM interventions i
            WHERE i.equipment_id = e.id
            AND i.status = 'in_progress'
        )
        THEN 'Maintenance en cours'
        WHEN EXISTS (
            SELECT 1 FROM interventions i
            WHERE i.equipment_id = e.id
            AND i.status = 'planned'
        )
        THEN 'Planifiée'
        WHEN EXISTS (
            SELECT 1 FROM declarations_panne d
            WHERE d.equipment_id = e.id
            AND d.status IN ('pending', 'in_progress')
        )
        THEN 'Problème'
        ELSE 'Opérationnel'
        END as etat
    FROM equipements e
    LEFT JOIN clients c ON e.client_id = c.id
    """

    params = []

    if selected_client:
        query += " WHERE e.client_id = ?"
        params.append(selected_client)

    cursor.execute(query, params)
    equipements_etat = cursor.fetchall()

    maintenance_today = sum(
        1 for e in equipements_etat
        if e[3] in ("Maintenance en cours", "Problème")
    )

    # BEGIN REAL_AVAILABILITY_METRICS
    # ==========================
    # INDICATEURS MAINTENANCE
    # Disponibilité = temps d'ouverture client - indisponibilité réelle.
    # Une indisponibilité commence à la date/heure prévue et s'arrête à la
    # première soumission du rapport. Sans rapport, elle court jusqu'à maintenant.
    # ==========================

    base_where = ""
    base_params = []
    if selected_client:
        base_where = "WHERE e.client_id = ?"
        base_params = [selected_client]

    period_days = 30
    period_end_dt = datetime.now()
    period_start_dt = period_end_dt - timedelta(days=period_days)

    availability_metrics = calculate_availability_metrics(
        conn,
        period_start=period_start_dt,
        period_end=period_end_dt,
        selected_client=selected_client,
    )

    cursor.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN i.status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN i.status = 'planned' THEN 1 ELSE 0 END) AS planned,
            SUM(CASE WHEN i.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN i.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN i.status = 'postponed' THEN 1 ELSE 0 END) AS postponed
        FROM interventions i
        JOIN equipements e ON i.equipment_id = e.id
        {base_where}
        """,
        base_params,
    )
    row = cursor.fetchone() or (0, 0, 0, 0, 0, 0)
    global_total, global_completed, global_planned, global_in_progress, global_cancelled, global_postponed = [
        int(v or 0) for v in row
    ]

    cursor.execute(
        f"""
        SELECT
            c.id,
            COALESCE(c.nom, 'Sans client') AS client_nom,
            COALESCE(c.rythme_horaire, '1x8') AS rythme_horaire,
            COUNT(DISTINCT e.id) AS equipment_count,
            COUNT(i.id) AS total,
            SUM(CASE WHEN i.status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN i.status = 'planned' THEN 1 ELSE 0 END) AS planned,
            SUM(CASE WHEN i.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN i.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN i.status = 'postponed' THEN 1 ELSE 0 END) AS postponed
        FROM equipements e
        LEFT JOIN clients c ON e.client_id = c.id
        LEFT JOIN interventions i ON i.equipment_id = e.id
        {base_where}
        GROUP BY c.id, c.nom, c.rythme_horaire
        ORDER BY client_nom ASC
        """,
        base_params,
    )

    indicateurs_clients = []
    for client_row in cursor.fetchall():
        client_id = client_row[0]
        metric = availability_metrics["clients"].get(client_id, {})
        indicateurs_clients.append({
            "id": client_id,
            "nom": client_row[1],
            "rythme_horaire": normalize_rythme(client_row[2]),
            "equipment_count": int(client_row[3] or 0),
            "total": int(client_row[4] or 0),
            "completed": int(client_row[5] or 0),
            "planned": int(client_row[6] or 0),
            "in_progress": int(client_row[7] or 0),
            "cancelled": int(client_row[8] or 0),
            "postponed": int(client_row[9] or 0),
            "downtime_minutes": int(metric.get("downtime_minutes", 0)),
            "disponibilite": float(metric.get("rate", 100.0)),
        })

    global_rate = availability_metrics["global_rate"]

    cursor.execute(
        f"""
        SELECT
            e.id,
            e.nom,
            COALESCE(c.nom, 'Sans client') AS client_nom,
            COUNT(i.id) AS total,
            SUM(CASE WHEN i.status = 'completed' THEN 1 ELSE 0 END) AS completed,
            SUM(CASE WHEN i.status = 'planned' THEN 1 ELSE 0 END) AS planned,
            SUM(CASE WHEN i.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress,
            SUM(CASE WHEN i.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled,
            SUM(CASE WHEN i.status = 'postponed' THEN 1 ELSE 0 END) AS postponed
        FROM equipements e
        LEFT JOIN clients c ON e.client_id = c.id
        LEFT JOIN interventions i ON i.equipment_id = e.id
        {base_where}
        GROUP BY e.id, e.nom, c.nom
        ORDER BY client_nom ASC, e.nom ASC
        """,
        base_params,
    )

    indicateurs_equipements = []
    for eq_row in cursor.fetchall():
        metric = availability_metrics["equipements"].get(eq_row[0], {})
        indicateurs_equipements.append({
            "id": eq_row[0],
            "nom": eq_row[1],
            "client_nom": eq_row[2],
            "total": int(eq_row[3] or 0),
            "completed": int(eq_row[4] or 0),
            "planned": int(eq_row[5] or 0),
            "in_progress": int(eq_row[6] or 0),
            "cancelled": int(eq_row[7] or 0),
            "postponed": int(eq_row[8] or 0),
            "downtime_minutes": int(metric.get("downtime_minutes", 0)),
            "rate": float(metric.get("rate", 100.0)),
        })
    # END REAL_AVAILABILITY_METRICS

    conn.close()

    return render_template(
        "dashboard.html",
        nb_equipements=nb_equipements,
        en_cours=in_progress,
        planifiees=planned,
        interventions=segmented,
        maintenance_today=maintenance_today,
        equipements_etat=equipements_etat,
        clients=clients,
        selected_client=selected_client,
        maintenance_global={
            "total": global_total,
            "completed": global_completed,
            "planned": global_planned,
            "in_progress": global_in_progress,
            "cancelled": global_cancelled,
            "postponed": global_postponed,
            "rate": global_rate,
        },
        indicateurs_clients=indicateurs_clients,
        indicateurs_equipements=indicateurs_equipements,
    )
# ==========================
# ÉQUIPEMENTS
# ==========================

@app.route("/equipements")
@login_required
def equipements():
    recherche = request.args.get("q", "").strip()
    client_filtre = request.args.get("client_id", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    query = """
        SELECT equipements.id,
               equipements.nom,
               equipements.type,
               equipements.numero_serie,
               equipements.emplacement,
               equipements.code,
               equipements.statut,
               clients.id,
               clients.nom
        FROM equipements
        LEFT JOIN clients ON equipements.client_id = clients.id
    """

    conditions = []
    params = []

    if recherche:
        conditions.append("""(
            LOWER(equipements.nom) LIKE ?
            OR LOWER(COALESCE(equipements.code, '')) LIKE ?
            OR LOWER(COALESCE(equipements.type, '')) LIKE ?
            OR LOWER(COALESCE(equipements.numero_serie, '')) LIKE ?
            OR LOWER(COALESCE(equipements.emplacement, '')) LIKE ?
            OR LOWER(COALESCE(clients.nom, '')) LIKE ?
        )""")
        term = f"%{recherche.lower()}%"
        params.extend([term] * 6)

    if client_filtre:
        conditions.append("clients.id = ?")
        params.append(client_filtre)

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    query += " ORDER BY COALESCE(clients.nom, 'Sans client') ASC, equipements.nom ASC"

    cursor.execute(query, params)
    equipements = cursor.fetchall()

    equipements_par_client = {}
    for eq in equipements:
        client_nom = eq[8] if eq[8] else "Sans client"
        equipements_par_client.setdefault(client_nom, []).append(eq)

    cursor.execute("SELECT id, nom FROM clients ORDER BY nom ASC")

    clients = cursor.fetchall()

    conn.close()

    return render_template(
        "equipements.html",
        equipements=equipements,
        equipements_par_client=equipements_par_client,
        clients=clients,
        recherche=recherche,
        client_filtre=client_filtre
    )

@app.route("/equipements/add", methods=["POST"])
def add_equipement():
    nom = request.form["nom"]
    type_eq = request.form["type"]
    numero_serie = request.form["numero_serie"]
    emplacement = request.form["localisation"]
    client_id = request.form["client_id"]

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO equipements (nom, type, numero_serie, localisation, client_id)
        VALUES (?, ?, ?, ?, ?)
    """, (nom, type_eq, numero_serie, localisation, client_id))

    conn.commit()
    conn.close()

    return redirect("/equipements")

@app.route("/equipements/delete/<int:id>")
def delete_equipement(id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM equipements WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect("/equipements")

@app.route("/modifier_equipement/<int:id>", methods=["GET", "POST"])
@login_required
def modifier_equipement(id):

    conn = get_db_connection()
    conn.row_factory = dict
    cursor = conn.cursor()

    if request.method == "POST":

        nom = request.form["nom"]
        code = request.form["code"]
        type_eq = request.form["type"]
        statut = request.form["statut"]
        emplacement = request.form["emplacement"]
        client_id = request.form["client_id"]
        fabricant = request.form["fabricant"]
        modele = request.form["modele"]
        numero_serie = request.form["numero_serie"]
        date_installation = request.form["date_installation"]

        # Gestion photo
        photo_file = request.files.get("photo")

        if photo_file and photo_file.filename != "":
            filename = secure_filename(photo_file.filename)
            photo_path = "static/uploads/photos/" + filename
            photo_file.save(photo_path)

            cursor.execute("""
                UPDATE equipements
                SET photo=?
                WHERE id=?
            """, (photo_path, id))

        cursor.execute("""
            UPDATE equipements
            SET nom=?, code=?, type=?, statut=?, emplacement=?, 
                client_id=?, fabricant=?, modele=?, 
                numero_serie=?, date_installation=?
            WHERE id=?
        """, (
            nom, code, type_eq, statut, emplacement,
            client_id, fabricant, modele,
            numero_serie, date_installation, id
        ))

        # Nouveaux documents
        documents = request.files.getlist("documents")

        for doc in documents:
            if doc and doc.filename != "":
                filename = secure_filename(doc.filename)
                filepath = "static/uploads/documents/" + filename
                doc.save(filepath)

                cursor.execute("""
                    INSERT INTO equipement_documents
                    (equipement_id, filename, filepath)
                    VALUES (?, ?, ?)
                """, (id, filename, filepath))

        conn.commit()
        conn.close()

        return redirect(f"/equipements/{id}")

    # GET
    cursor.execute("SELECT * FROM equipements WHERE id=?", (id,))
    equipement = cursor.fetchone()

    cursor.execute("SELECT id, nom FROM clients")
    clients = cursor.fetchall()

    cursor.execute("""
        SELECT * FROM equipement_documents
        WHERE equipement_id=?
    """, (id,))
    documents = cursor.fetchall()

    conn.close()

    return render_template(
        "modifier_equipement.html",
        equipement=equipement,
        clients=clients,
        documents=documents
    )

@app.route("/equipements/nouveau", methods=["GET", "POST"])
@login_required
def nouveau_equipement():

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":

        nom = request.form["nom"]
        code = request.form["code"]
        type_eq = request.form["type"]
        statut = request.form["statut"]
        emplacement = request.form["emplacement"]
        client_id = request.form["client_id"]
        fabricant = request.form["fabricant"]
        modele = request.form["modele"]
        numero_serie = request.form["numero_serie"]
        date_installation = request.form["date_installation"]

        photo_file = request.files.get("photo")
        photo_path = None

        if photo_file and photo_file.filename != "":
            filename = secure_filename(photo_file.filename)
            photo_path = "static/uploads/photos/" + filename
            photo_file.save(photo_path)

        cursor.execute("""
            INSERT INTO equipements
            (nom, code, type, statut, emplacement, client_id,
             fabricant, modele, numero_serie, date_installation, photo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            nom, code, type_eq, statut, emplacement, client_id,
            fabricant, modele, numero_serie, date_installation, photo_path
        ))

        equipement_id = cursor.lastrowid

        # Documents multiples
        documents = request.files.getlist("documents")

        for doc in documents:
            if doc and doc.filename != "":
                filename = secure_filename(doc.filename)
                filepath = "static/uploads/documents/" + filename
                doc.save(filepath)

                cursor.execute("""
                    INSERT INTO equipement_documents
                    (equipement_id, filename, filepath)
                    VALUES (?, ?, ?)
                """, (equipement_id, filename, filepath))

        conn.commit()
        conn.close()

        return redirect("/equipements")

    cursor.execute("SELECT id, nom FROM clients")
    clients = cursor.fetchall()
    conn.close()

    return render_template("nouvel_equipement.html", clients=clients)

@app.route("/equipements/<int:id>")
@login_required
def fiche_equipement(id):

    conn = get_db_connection()
    conn.row_factory = dict
    cursor = conn.cursor()

    cursor.execute("""
        SELECT equipements.*, clients.nom as client_nom
        FROM equipements
        LEFT JOIN clients ON equipements.client_id = clients.id
        WHERE equipements.id=?
    """, (id,))
    equipement = cursor.fetchone()

    cursor.execute("""
        SELECT * FROM equipement_documents
        WHERE equipement_id=?
    """, (id,))
    documents = cursor.fetchall()

    conn.close()

    return render_template(
        "fiche_equipement.html",
        equipement=equipement,
        documents=documents
    )
@app.route("/export/equipements")
@login_required
def export_equipements():

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT e.nom,
               e.code,
               e.type,
               e.statut,
               e.emplacement,
               c.nom
        FROM equipements e
        LEFT JOIN clients c ON e.client_id = c.id
    """)

    rows = cursor.fetchall()
    conn.close()

    def generate():
        yield "Nom,Code,Type,Statut,Emplacement,Client\n"
        for r in rows:
            yield ",".join([str(x) if x else "" for x in r]) + "\n"

    return Response(generate(),
        mimetype="text/csv",
        headers={"Content-Disposition":"attachment;filename=equipements.csv"})
# ==========================
# TECHNICIENS
# ==========================

@app.route("/techniciens")
@login_required
def techniciens():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, nom, prenom, code, specialite, statut
        FROM techniciens
    """)

    techniciens = cursor.fetchall()
    conn.close()

    return render_template("techniciens.html", techniciens=techniciens)


@app.route("/techniciens/add", methods=["POST"])
def add_technicien():
    nom = request.form["nom"]
    prenom = request.form["prenom"]
    code = request.form["code"]
    specialite = request.form["specialite"]
    statut = request.form["statut"]

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO techniciens (nom, prenom, code, specialite, statut)
        VALUES (?, ?, ?, ?, ?)
    """, (nom, prenom, code, specialite, statut))

    conn.commit()
    conn.close()

    return redirect("/techniciens")


@app.route("/techniciens/delete/<int:id>")
def delete_technicien(id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM interventions WHERE assigned_to = ?", (id,))
    intervention_count = int(cursor.fetchone()[0] or 0)

    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name = 'technicien_user_links'
        )
        """
    )
    link_table_exists = bool(cursor.fetchone()[0])
    linked_account = False
    if link_table_exists:
        cursor.execute("SELECT COUNT(*) FROM technicien_user_links WHERE technicien_id = ?", (id,))
        linked_account = int(cursor.fetchone()[0] or 0) > 0

    if intervention_count > 0 or linked_account:
        conn.close()
        return redirect("/techniciens?error=used")

    cursor.execute("DELETE FROM techniciens WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect("/techniciens")

@app.route("/modifier_technicien/<int:id>", methods=["GET", "POST"])
def modifier_technicien(id):
    conn = get_db_connection()
    conn.row_factory = dict
    cursor = conn.cursor()

    if request.method == "POST":
        nom = request.form["nom"]
        prenom = request.form["prenom"]
        code = request.form["code"]
        specialite = request.form["specialite"]
        statut = request.form["statut"]

        cursor.execute("""
            UPDATE techniciens
            SET nom=?, prenom=?, code=?, specialite=?, statut=?
            WHERE id=?
        """, (nom, prenom, code, specialite, statut, id))

        conn.commit()
        conn.close()
        return redirect("/techniciens")

    cursor.execute("SELECT * FROM techniciens WHERE id=?", (id,))
    technicien = cursor.fetchone()
    conn.close()

    return render_template("modifier_technicien.html", technicien=technicien)

# ==========================
# Intervention
# ==========================

@app.route("/interventions")
@login_required
def interventions():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, nom FROM equipements")
    equipements = cursor.fetchall()

    cursor.execute("SELECT id, code FROM techniciens")
    techniciens = cursor.fetchall()

    cursor.execute("""
        SELECT interventions.id,
               interventions.title,
               interventions.type,
               interventions.priority,
               interventions.status,
               interventions.scheduled_date,
               interventions.scheduled_time,
               equipements.nom,
               techniciens.code
        FROM interventions
        LEFT JOIN equipements ON interventions.equipment_id = equipements.id
        LEFT JOIN techniciens ON interventions.assigned_to = techniciens.id
        ORDER BY interventions.scheduled_date ASC
    """)

    interventions = cursor.fetchall()
    conn.close()

    return render_template(
        "interventions.html",
        interventions=interventions,
        equipements=equipements,
        techniciens=techniciens
    )
@app.route("/interventions/nouvelle")
def nouvelle_intervention():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, nom FROM equipements")
    equipements = cursor.fetchall()

    cursor.execute("SELECT id, code FROM techniciens WHERE statut='Actif'")
    techniciens = cursor.fetchall()

    conn.close()

    return render_template(
        "nouvelle_intervention.html",
        equipements=equipements,
        techniciens=techniciens
    )

@app.route("/interventions/add", methods=["POST"])
def add_intervention():
    data = request.form

    # Conversion heures -> minutes
    duration_hours = float(data.get("estimated_duration_hours", 0))
    duration_minutes = int(duration_hours * 60)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO interventions
        (title, equipment_id, routine_id, type, priority, status,
         scheduled_date, scheduled_time, assigned_to,
         estimated_duration, description)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["title"],
        data["equipment_id"],
        data.get("routine_id"),
        data["type"],
        data.get("priority", "medium"),
        data.get("status", "planned"),
        data["scheduled_date"],
        data.get("scheduled_time"),
        data.get("assigned_to"),
        duration_minutes,
        data.get("description")
    ))

    sync_equipement_statut(conn, data["equipment_id"])

    conn.commit()
    conn.close()

    return redirect("/interventions")
@app.route("/interventions/delete/<int:id>")
def delete_intervention(id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT equipment_id FROM interventions WHERE id=?", (id,))
    row = cursor.fetchone()

    cursor.execute("DELETE FROM interventions WHERE id=?", (id,))

    if row:
        sync_equipement_statut(conn, row[0])

    conn.commit()
    conn.close()
    return redirect("/interventions")

@app.route("/interventions/update_status/<int:id>/<status>")
def update_status(id, status):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT equipment_id FROM interventions WHERE id=?", (id,))
    row = cursor.fetchone()

    if status == "completed":
        cursor.execute("""
            UPDATE interventions
            SET status=?, completion_date=date('now')
            WHERE id=?
        """, (status, id))
    else:
        cursor.execute("""
            UPDATE interventions
            SET status=?
            WHERE id=?
        """, (status, id))

    if row:
        sync_equipement_statut(conn, row[0])

    conn.commit()
    conn.close()

    return redirect("/interventions")

@app.route("/interventions/<int:id>/details")
@login_required
def intervention_details(id):

    conn = get_db_connection()
    conn.row_factory = dict
    cursor = conn.cursor()

    cursor.execute("""
        SELECT interventions.*,
               equipements.nom as equipement_nom,
               techniciens.code as technicien_code
        FROM interventions
        LEFT JOIN equipements ON interventions.equipment_id = equipements.id
        LEFT JOIN techniciens ON interventions.assigned_to = techniciens.id
        WHERE interventions.id=?
    """, (id,))

    intervention = cursor.fetchone()
    conn.close()

    if not intervention:
        return {"error": "Not found"}, 404

    return dict(intervention)


@app.route("/rapports")
@login_required
def rapports():
    q = request.args.get("q", "").strip()
    etat = request.args.get("etat", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM rapports_intervention")
    k_total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM rapports_intervention WHERE etat='Opérationnel'")
    k_ok = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM rapports_intervention WHERE etat='Nécessite un suivi'")
    k_suivi = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM rapports_intervention WHERE etat='Toujours en panne'")
    k_ko = cursor.fetchone()[0]

    query = """
        SELECT r.id,
               i.title,
               e.nom,
               r.travaux,
               r.heure_debut,
               r.heure_fin,
               r.etat,
               r.created_at,
               COALESCE(u.username, '-')
        FROM rapports_intervention r
        LEFT JOIN interventions i ON r.intervention_id = i.id
        LEFT JOIN equipements e ON i.equipment_id = e.id
        LEFT JOIN users u ON r.created_by_user_id = u.id
        WHERE 1=1
    """
    params = []

    if etat:
        query += " AND r.etat = ?"
        params.append(etat)

    if q:
        query += " AND (i.title LIKE ? OR e.nom LIKE ? OR r.travaux LIKE ? OR r.observations LIKE ?)"
        like = f"%{q}%"
        params.extend([like, like, like, like])

    query += " ORDER BY r.created_at DESC"

    cursor.execute(query, params)
    rapports = cursor.fetchall()
    conn.close()

    return render_template(
        "rapports.html",
        rapports=rapports,
        q=q,
        etat=etat,
        k_total=k_total,
        k_ok=k_ok,
        k_suivi=k_suivi,
        k_ko=k_ko,
    )



# BEGIN INTERVENTION_REPORT_PDF_EXPORT
@app.route("/rapports/<int:id>/pdf")
@login_required
def rapport_pdf(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.id,
               r.intervention_id,
               r.travaux,
               r.heure_debut,
               r.heure_fin,
               r.observations,
               r.etat,
               r.recommandations,
               r.created_at,
               COALESCE(u.username, '-'),
               i.title,
               i.type,
               i.priority,
               i.status,
               i.scheduled_date,
               i.scheduled_time,
               i.estimated_duration,
               e.id,
               e.nom,
               e.code,
               e.type,
               e.emplacement,
               e.numero_serie,
               e.fabricant,
               e.modele,
               c.nom,
               c.email,
               c.telephone,
               t.nom,
               t.prenom,
               t.code
        FROM rapports_intervention r
        LEFT JOIN interventions i ON i.id = r.intervention_id
        LEFT JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN clients c ON c.id = e.client_id
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        LEFT JOIN users u ON u.id = r.created_by_user_id
        WHERE r.id = ?
        """,
        (id,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Rapport introuvable", 404

    technician_name = " ".join(
        part for part in [row[29], row[28]] if part
    ).strip() or row[9] or "-"

    data = {
        "report_id": row[0],
        "intervention_id": row[1],
        "travaux": row[2],
        "heure_debut": row[3],
        "heure_fin": row[4],
        "observations": row[5],
        "etat": row[6],
        "recommandations": row[7],
        "created_at": row[8],
        "author": row[9],
        "intervention_title": row[10],
        "intervention_type": row[11],
        "priority": row[12],
        "intervention_status": row[13],
        "scheduled_date": row[14],
        "scheduled_time": row[15],
        "estimated_duration": row[16],
        "equipment_id": row[17],
        "equipment_name": row[18],
        "equipment_code": row[19],
        "equipment_type": row[20],
        "equipment_location": row[21],
        "serial_number": row[22],
        "manufacturer": row[23],
        "model": row[24],
        "client_name": row[25],
        "client_email": row[26],
        "client_phone": row[27],
        "technician_name": technician_name,
        "technician_code": row[30],
        "work_date": row[14],
    }

    materials = []
    cursor.execute("SELECT to_regclass('public.intervention_stock_items')")
    stock_table = cursor.fetchone()
    if stock_table and stock_table[0] and row[1]:
        cursor.execute(
            """
            SELECT a.designation,
                   a.reference,
                   isi.quantite_utilisee,
                   a.unite
            FROM intervention_stock_items isi
            LEFT JOIN stock_articles a ON a.id = isi.article_id
            WHERE isi.intervention_id = ?
            ORDER BY isi.created_at ASC, isi.id ASC
            """,
            (row[1],),
        )
        for material_row in cursor.fetchall():
            quantity = str(material_row[2] or "-")
            if material_row[3]:
                quantity = f"{quantity} {material_row[3]}"
            materials.append(
                {
                    "designation": material_row[0] or "-",
                    "reference": material_row[1] or "-",
                    "quantite": quantity,
                }
            )

    conn.close()
    output = create_intervention_report_pdf(data, materials=materials)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"Rapport_Intervention_{id}.pdf",
        mimetype="application/pdf",
    )
# END INTERVENTION_REPORT_PDF_EXPORT

@app.route("/rapports/<int:id>/details")
@login_required
def rapport_details(id):
    conn = get_db_connection()
    conn.row_factory = dict
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT r.*, i.title AS intervention_title, e.nom AS equipement_nom,
               COALESCE(u.username, '-') AS auteur
        FROM rapports_intervention r
        LEFT JOIN interventions i ON r.intervention_id = i.id
        LEFT JOIN equipements e ON i.equipment_id = e.id
        LEFT JOIN users u ON r.created_by_user_id = u.id
        WHERE r.id=?
        """,
        (id,),
    )
    rapport = cursor.fetchone()
    conn.close()

    if not rapport:
        return {"error": "Not found"}, 404

    return dict(rapport)


@app.route("/rapports/add", methods=["POST"])
@login_required
@role_required("admin", "technician")
def add_rapport():
    data = request.form
    intervention_id = data.get("intervention_id")

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT equipment_id FROM interventions WHERE id=?", (intervention_id,))
    intervention = cursor.fetchone()

    if not intervention:
        conn.close()
        return "Intervention introuvable", 404

    cursor.execute(
        """
        INSERT INTO rapports_intervention
        (intervention_id, travaux, heure_debut, heure_fin, observations, etat, recommandations, created_by_user_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intervention_id,
            data.get("travaux"),
            data.get("heure_debut") or None,
            data.get("heure_fin"),
            data.get("observations"),
            data.get("etat"),
            data.get("recommandations"),
            session.get("user_id"),
        ),
    )

    cursor.execute(
        """
        UPDATE interventions
        SET status='completed', completion_date=date('now')
        WHERE id=?
        """,
        (intervention_id,),
    )

    equipement_id = intervention[0]
    etat_rapport = data.get("etat")

    # Si le rapport confirme que l'équipement est opérationnel,
    # on clôture automatiquement la déclaration de panne liée à cette intervention.
    if etat_rapport == "Opérationnel":
        cursor.execute(
            """
            UPDATE declarations_panne
            SET status='resolved', updated_at=datetime('now')
            WHERE intervention_id=?
              AND status IN ('pending', 'in_progress')
            """,
            (intervention_id,),
        )

    if etat_rapport == "Toujours en panne":
        cursor.execute("UPDATE equipements SET statut='Problème' WHERE id=?", (equipement_id,))
    else:
        sync_equipement_statut(conn, equipement_id)

    conn.commit()
    conn.close()
    return redirect("/rapports")


@app.route("/rapports/<int:id>/delete")
@login_required
@role_required("admin")
def delete_rapport(id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT i.id, i.equipment_id
        FROM rapports_intervention r
        LEFT JOIN interventions i ON r.intervention_id = i.id
        WHERE r.id=?
        """,
        (id,),
    )
    row = cursor.fetchone()

    cursor.execute("DELETE FROM rapports_intervention WHERE id=?", (id,))

    if row:
        intervention_id, equipement_id = row
        cursor.execute(
            "UPDATE interventions SET status='in_progress', completion_date=NULL WHERE id=?",
            (intervention_id,),
        )
        sync_equipement_statut(conn, equipement_id)

    conn.commit()
    conn.close()
    return redirect("/rapports")


@app.route("/export/rapports")
@login_required
def export_rapports():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT r.id,
               i.title,
               e.nom,
               r.travaux,
               r.heure_debut,
               r.heure_fin,
               r.etat,
               r.observations,
               r.recommandations,
               r.created_at
        FROM rapports_intervention r
        LEFT JOIN interventions i ON r.intervention_id = i.id
        LEFT JOIN equipements e ON i.equipment_id = e.id
        ORDER BY r.created_at DESC
        """
    )
    rows = cursor.fetchall()
    conn.close()

    output = []
    headers = [
        "id_rapport",
        "intervention",
        "equipement",
        "travaux",
        "heure_debut",
        "heure_fin",
        "etat",
        "observations",
        "recommandations",
        "cree_le",
    ]
    output.append(",".join(headers))
    for row in rows:
        output.append(",".join([str(col or "").replace(",", " ") for col in row]))

    return Response(
        "\n".join(output),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=rapports_intervention.csv"},
    )
from flask import send_from_directory

@app.route("/declarations")
@login_required
def declarations():

    q = request.args.get("q", "").strip()
    status = request.args.get("status", "").strip()

    conn = get_db_connection()
    cursor = conn.cursor()

    # KPIs
    cursor.execute("SELECT COUNT(*) FROM declarations_panne WHERE status='pending'")
    k_pending = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM declarations_panne WHERE status='in_progress'")
    k_progress = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM declarations_panne WHERE status='resolved'")
    k_resolved = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM declarations_panne WHERE status='rejected'")
    k_rejected = cursor.fetchone()[0]

    query = """
        SELECT d.id,
               d.title,
               d.description,
               d.urgency,
               d.location,
               d.status,
               d.created_at,
               e.nom,
               e.code,
               u.username,
               d.declared_by_name,
               d.intervention_id
        FROM declarations_panne d
        LEFT JOIN equipements e ON d.equipment_id = e.id
        LEFT JOIN users u ON d.declared_by_user_id = u.id
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND d.status = ?"
        params.append(status)

    if q:
        query += " AND (d.title LIKE ? OR d.description LIKE ? OR e.nom LIKE ? OR e.code LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like, like]

    query += " ORDER BY d.created_at DESC"

    cursor.execute(query, params)
    declarations = cursor.fetchall()

    conn.close()

    return render_template(
        "declarations.html",
        declarations=declarations,
        q=q,
        status=status,
        k_pending=k_pending,
        k_progress=k_progress,
        k_resolved=k_resolved,
        k_rejected=k_rejected
    )
    
# BEGIN DECLARATION_PDF_EXPORT
@app.route("/declarations/<int:id>/pdf")
@login_required
def export_declaration_pdf(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT d.id,
               d.title,
               d.description,
               d.urgency,
               d.location,
               d.status,
               d.created_at,
               d.declared_by_name,
               e.nom,
               e.code,
               e.type,
               e.emplacement,
               e.numero_serie,
               e.fabricant,
               e.modele,
               c.nom,
               u.username,
               d.intervention_id,
               i.title
        FROM declarations_panne d
        LEFT JOIN equipements e ON e.id = d.equipment_id
        LEFT JOIN clients c ON c.id = e.client_id
        LEFT JOIN users u ON u.id = d.declared_by_user_id
        LEFT JOIN interventions i ON i.id = d.intervention_id
        WHERE d.id = ?
        """,
        (id,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Déclaration introuvable", 404

    cursor.execute(
        "SELECT filepath FROM declaration_photos WHERE declaration_id = ? ORDER BY id ASC",
        (id,),
    )
    photo_paths = [photo[0] for photo in cursor.fetchall() if photo and photo[0]]
    conn.close()

    data = {
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "urgency": row[3],
        "location": row[4],
        "status": row[5],
        "created_at": row[6],
        "declared_by_name": row[7],
        "equipement_nom": row[8],
        "equipement_code": row[9],
        "equipement_type": row[10],
        "equipement_emplacement": row[11],
        "numero_serie": row[12],
        "fabricant": row[13],
        "modele": row[14],
        "client_nom": row[15],
        "username": row[16],
        "intervention_id": row[17],
        "intervention_title": row[18],
    }

    pdf_file = create_declaration_pdf(
        data,
        photo_paths=photo_paths,
        base_dir=os.path.dirname(os.path.abspath(__file__)),
    )
    return send_file(
        pdf_file,
        as_attachment=True,
        download_name=f"Declaration_panne_{id}.pdf",
        mimetype="application/pdf",
    )
# END DECLARATION_PDF_EXPORT

@app.route("/declarations/nouvelle", methods=["GET", "POST"])
@login_required
@role_required("operator", "admin", "technician")  # un tech peut aussi déclarer si besoin
def nouvelle_declaration():

    conn = get_db_connection()
    cursor = conn.cursor()

    if request.method == "POST":

        equipment_id = request.form["equipment_id"]
        declared_by_name = request.form.get("declared_by_name", "").strip()
        title = request.form["title"]
        description = request.form["description"]
        location = request.form.get("location", "").strip()

        cursor.execute("""
            INSERT INTO declarations_panne
            (equipment_id, declared_by_user_id, declared_by_name, title, description, urgency, location)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            equipment_id,
            session.get("user_id"),
            declared_by_name,
            title,
            description,
            "medium",
            location
        ))

        sync_equipement_statut(conn, equipment_id)

        declaration_id = cursor.lastrowid

        # Photos (optionnel)
        photos = request.files.getlist("photos")
        for p in photos:
            if p and p.filename:
                filename = secure_filename(p.filename)
                filepath = f"static/uploads/pannes/{declaration_id}_{filename}"
                p.save(filepath)
                cursor.execute("""
                    INSERT INTO declaration_photos (declaration_id, filepath)
                    VALUES (?, ?)
                """, (declaration_id, filepath))

        conn.commit()
        conn.close()

        return redirect("/declarations")

    cursor.execute("SELECT id, nom, code FROM equipements ORDER BY nom ASC")
    equipements = cursor.fetchall()

    conn.close()
    return render_template("nouvelle_declaration.html", equipements=equipements)


@app.route("/declarations/<int:id>/status/<status>")
@login_required
@role_required("technician", "admin")
def declaration_set_status(id, status):

    if status not in ("pending", "in_progress", "resolved", "rejected"):
        return "Statut invalide", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT equipment_id, status, intervention_id FROM declarations_panne WHERE id=?",
        (id,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Déclaration introuvable", 404

    equipment_id, current_status, intervention_id = row
    declaration_locked = current_status in ("in_progress", "resolved", "rejected") or intervention_id is not None
    if declaration_locked:
        conn.close()
        return "Cette déclaration est verrouillée, changement de statut impossible.", 400

    cursor.execute("""
        UPDATE declarations_panne
        SET status=?, updated_at=datetime('now')
        WHERE id=?
    """, (status, id))

    sync_equipement_statut(conn, equipment_id)

    conn.commit()
    conn.close()
    return redirect("/declarations")


@app.route("/declarations/<int:id>/create_intervention", methods=["GET", "POST"])
@login_required
@role_required("technician", "admin")
def declaration_create_intervention(id):

    conn = get_db_connection()
    cursor = conn.cursor()

    # Récup déclaration
    cursor.execute("""
        SELECT d.id, d.title, d.description, d.urgency, d.location, d.equipment_id,
               e.nom, e.code, d.status, d.intervention_id
        FROM declarations_panne d
        LEFT JOIN equipements e ON d.equipment_id = e.id
        WHERE d.id=?
    """, (id,))
    dec = cursor.fetchone()
    if not dec:
        conn.close()
        return "Déclaration introuvable", 404

    if dec[8] in ("in_progress", "resolved", "rejected") or dec[9] is not None:
        conn.close()
        return "Cette déclaration est verrouillée, création d'intervention impossible.", 400

    cursor.execute("SELECT id, code FROM techniciens WHERE statut='Actif'")
    techniciens = cursor.fetchall()

    if request.method == "POST":

        title = request.form["title"]
        assigned_to = request.form.get("assigned_to") or None
        scheduled_date = request.form["scheduled_date"]
        scheduled_time = request.form.get("scheduled_time") or None
        priority = request.form.get("priority", "medium")
        description = request.form.get("description", "")

        # durée heures -> minutes (comme ton code)
        duration_hours = float(request.form.get("estimated_duration_hours", 0) or 0)
        duration_minutes = int(duration_hours * 60)

        cursor.execute("""
            INSERT INTO interventions
            (title, equipment_id, type, priority, status, scheduled_date, scheduled_time, assigned_to, estimated_duration, description)
            VALUES (?, ?, 'corrective', ?, 'planned', ?, ?, ?, ?, ?)
        """, (
            title,
            dec[5],
            priority,
            scheduled_date,
            scheduled_time,
            assigned_to,
            duration_minutes,
            description
        ))
        intervention_id = cursor.lastrowid

        # Lier + passer la déclaration en "in_progress"
        cursor.execute("""
            UPDATE declarations_panne
            SET intervention_id=?, status='in_progress', updated_at=datetime('now')
            WHERE id=?
        """, (intervention_id, id))

        sync_equipement_statut(conn, dec[5])

        conn.commit()
        conn.close()

        return redirect("/interventions")

    # GET: pré-remplissage “smart”
    default_priority = dec[3]  # urgency -> priority (mêmes valeurs)
    prefilled_title = f"[Panne] {dec[1]}"

    prefilled_desc = (dec[2] or "")
    if dec[4]:
        prefilled_desc = f"Localisation: {dec[4]}\n\n" + prefilled_desc

    conn.close()

    return render_template(
        "declaration_to_intervention.html",
        dec=dec,
        techniciens=techniciens,
        default_priority=default_priority,
        prefilled_title=prefilled_title,
        prefilled_desc=prefilled_desc
    )


@app.route("/declarations/<int:id>/force_status/<status>")
@login_required
@role_required("admin")
def declaration_force_status(id, status):

    if status not in ("pending", "in_progress", "resolved", "rejected"):
        return "Statut invalide", 400

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT equipment_id FROM declarations_panne WHERE id=?", (id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Déclaration introuvable", 404

    cursor.execute("""
        UPDATE declarations_panne
        SET status=?, updated_at=datetime('now')
        WHERE id=?
    """, (status, id))

    sync_equipement_statut(conn, row[0])

    conn.commit()
    conn.close()
    return redirect("/declarations")
    
@app.route("/export/interventions")
@login_required
def export_interventions():

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT i.title,
               i.type,
               i.priority,
               i.status,
               i.scheduled_date,
               i.scheduled_time,
               i.estimated_duration,
               e.nom,
               c.nom
        FROM interventions i
        LEFT JOIN equipements e ON i.equipment_id = e.id
        LEFT JOIN clients c ON e.client_id = c.id
    """)

    rows = cursor.fetchall()
    conn.close()

    def generate():
        yield "Titre,Type,Priorité,Statut,Date,Heure,Durée(min),Equipement,Client\n"
        for r in rows:
            yield ",".join([str(x) if x else "" for x in r]) + "\n"

    return Response(generate(),
        mimetype="text/csv",
        headers={"Content-Disposition":"attachment;filename=interventions.csv"})
    
# ==========================
# Client
# ==========================

@app.route("/clients")
@login_required
def clients():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT clients.id,
               clients.nom,
               clients.email,
               clients.telephone,
               clients.site_web,
               COALESCE(clients.rythme_horaire, '1x8') as rythme_horaire,
               COALESCE(SUM(interventions.estimated_duration), 0)
        FROM clients
        LEFT JOIN equipements ON equipements.client_id = clients.id
        LEFT JOIN interventions ON interventions.equipment_id = equipements.id
        GROUP BY clients.id
    """)

    clients = cursor.fetchall()
    conn.close()

    return render_template("clients.html", clients=clients, rythme_options=RYTHME_OPTIONS)

@app.route("/clients/nouveau", methods=["GET", "POST"])
@login_required
def nouveau_client():
    if request.method == "POST":
        nom = request.form.get("nom")
        email = request.form.get("email")
        telephone = request.form.get("telephone")
        site_web = request.form.get("site_web")
        rythme_horaire = normalize_rythme(request.form.get("rythme_horaire"))

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO clients (nom, email, telephone, site_web, rythme_horaire)
            VALUES (?, ?, ?, ?, ?)
        """, (nom, email, telephone, site_web, rythme_horaire))
        conn.commit()
        conn.close()

        return redirect("/clients")

    return render_template("nouvel_client.html", rythme_options=RYTHME_OPTIONS)

@app.route("/clients/add", methods=["POST"])
def add_client():
    nom = request.form.get("nom")
    email = request.form.get("email")
    telephone = request.form.get("telephone")
    site_web = request.form.get("site_web")
    rythme_horaire = normalize_rythme(request.form.get("rythme_horaire"))

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO clients (nom, email, telephone, site_web, rythme_horaire)
        VALUES (?, ?, ?, ?, ?)
    """, (nom, email, telephone, site_web, rythme_horaire))
    conn.commit()
    conn.close()

    return redirect("/clients")

@app.route("/clients/delete/<int:id>")
def delete_client(id):
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM equipements WHERE client_id = ?", (id,))
    equipment_count = int(cursor.fetchone()[0] or 0)
    if equipment_count > 0:
        conn.close()
        return redirect("/clients?error=used")

    cursor.execute("DELETE FROM clients WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect("/clients")

@app.route("/modifier_client/<int:id>", methods=["GET", "POST"])
def modifier_client(id):
    conn = get_db_connection()
    conn.row_factory = dict
    cursor = conn.cursor()

    if request.method == "POST":
        nom = request.form["nom"]
        email = request.form["email"]
        telephone = request.form["telephone"]
        site_web = request.form.get("site_web")
        rythme_horaire = normalize_rythme(request.form.get("rythme_horaire"))


        cursor.execute("""
            UPDATE clients
            SET nom=?, email=?, telephone=?, site_web=?, rythme_horaire=?
            WHERE id=?
        """, (nom, email, telephone, site_web, rythme_horaire, id))

        conn.commit()
        conn.close()
        return redirect("/clients")

    cursor.execute("SELECT * FROM clients WHERE id=?", (id,))
    client = cursor.fetchone()
    conn.close()

    return render_template("modifier_client.html", client=client, rythme_options=RYTHME_OPTIONS)
@app.route("/export/clients")
@login_required
def export_clients():

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT clients.nom,
               clients.email,
               clients.telephone,
               COALESCE(clients.rythme_horaire, '1x8') as rythme_horaire,
               COALESCE(SUM(interventions.estimated_duration),0)
        FROM clients
        LEFT JOIN equipements ON equipements.client_id = clients.id
        LEFT JOIN interventions ON interventions.equipment_id = equipements.id
        GROUP BY clients.id
    """)

    rows = cursor.fetchall()
    conn.close()

    def generate():
        yield "Nom,Email,Telephone,Rythme_horaire,Heures_totales\n"
        for r in rows:
            heures = round(r[4] / 60, 2)
            yield f"{r[0]},{r[1]},{r[2]},{r[3]},{heures}\n"

    return Response(generate(),
        mimetype="text/csv",
        headers={"Content-Disposition":"attachment;filename=clients.csv"})
# ==========================
# Export
# ==========================
# BEGIN EXCEL_TREE_EXPORT
@app.route("/export/gmao-xlsx")
@login_required
def export_gmao_xlsx():
    template_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "GMAO.xlsx",
    )
    if not os.path.isfile(template_path):
        return "Modèle Excel GMAO.xlsx introuvable dans le dossier de l'application.", 500

    output = create_gmao_excel_export(get_db_connection, template_path)
    return send_file(
        output,
        as_attachment=True,
        download_name="Export_GMAO.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
# END EXCEL_TREE_EXPORT


# BEGIN REPORT_COMPLETION_INTEGRITY
@app.before_request
def prevent_duplicate_intervention_report_submission():
    """Empêche de créer plusieurs rapports pour une même intervention.

    L'interface masque normalement le bouton dès que l'intervention est terminée,
    mais cette protection serveur reste la source de vérité si un formulaire est
    soumis directement ou depuis une ancienne page encore ouverte.
    """
    if request.method != "POST" or request.path != "/rapports/add":
        return None

    intervention_id = request.form.get("intervention_id")
    if not intervention_id:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id
        FROM rapports_intervention
        WHERE intervention_id = ?
        ORDER BY id ASC
        LIMIT 1
        """,
        (intervention_id,),
    )
    existing = cursor.fetchone()
    conn.close()

    if existing:
        return (
            "Un rapport a déjà été soumis pour cette intervention. "
            "La création d'un second rapport est interdite.",
            409,
        )

    return None
# END REPORT_COMPLETION_INTEGRITY


# BEGIN STOCK_BEFORE_REPORT_WORKFLOW

def _stock_intervention_is_locked(conn, intervention_id):
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM interventions WHERE id = ?", (intervention_id,))
    row = cursor.fetchone()
    if not row:
        return None

    status = str(row[0] or "").lower()
    cursor.execute(
        "SELECT COUNT(*) FROM rapports_intervention WHERE intervention_id = ?",
        (intervention_id,),
    )
    has_report = int(cursor.fetchone()[0] or 0) > 0
    return status == "completed" or has_report


def _stock_intervention_has_open_reservations(conn, intervention_id):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM stock_reservations
        WHERE intervention_id = ? AND statut = 'reserved'
        """,
        (intervention_id,),
    )
    return int(cursor.fetchone()[0] or 0) > 0


@app.route("/stock/intervention/<int:intervention_id>/validate", methods=["POST"])
@login_required
@role_required("admin", "technician")
def validate_stock_before_report(intervention_id):
    conn = get_db_connection()
    if not _stock_can_manage_intervention(conn, intervention_id):
        conn.close()
        return "Accès refusé à cette intervention.", 403

    locked = _stock_intervention_is_locked(conn, intervention_id)
    if locked is None:
        conn.close()
        return "Intervention introuvable.", 404
    if locked:
        conn.close()
        return "Le rapport a déjà été soumis : les pièces sont verrouillées.", 409
    if _stock_intervention_has_open_reservations(conn, intervention_id):
        conn.close()
        return "Il reste une ou plusieurs réservations ouvertes. Consomme-les ou annule-les avant de rédiger le rapport.", 409
    conn.close()

    reviewed = list(session.get("stock_reviewed_interventions", []))
    intervention_id = int(intervention_id)
    if intervention_id not in reviewed:
        reviewed.append(intervention_id)
    session["stock_reviewed_interventions"] = reviewed[-100:]
    session.modified = True
    return redirect(f"/interventions?report={intervention_id}")


@app.before_request
def enforce_stock_before_report_workflow():
    if request.method != "POST":
        return None

    path = request.path

    # Le rapport ne peut être envoyé qu'après validation explicite de l'écran pièces.
    if path == "/rapports/add":
        raw_id = request.form.get("intervention_id")
        try:
            intervention_id = int(raw_id)
        except (TypeError, ValueError):
            return None

        reviewed = {int(value) for value in session.get("stock_reviewed_interventions", [])}
        if intervention_id not in reviewed:
            return (
                "Les pièces de cette intervention doivent être vérifiées avant de soumettre le rapport. "
                "Passe d'abord par 'Gérer les pièces'.",
                409,
            )

        conn = get_db_connection()
        if _stock_intervention_has_open_reservations(conn, intervention_id):
            conn.close()
            return (
                "Il reste une ou plusieurs réservations ouvertes. "
                "Consomme-les ou annule-les avant de soumettre le rapport.",
                409,
            )
        conn.close()
        return None

    # La validation de l'étape pièces doit naturellement rester autorisée.
    if path.startswith("/stock/intervention/") and path.endswith("/validate"):
        return None

    intervention_id = None

    # Réserver / consommer / retourner depuis une intervention.
    if path.startswith("/stock/intervention/"):
        parts = path.strip("/").split("/")
        if len(parts) >= 4:
            try:
                intervention_id = int(parts[2])
            except (TypeError, ValueError):
                intervention_id = None

    # Annulation d'une réservation.
    elif path.startswith("/stock/reservations/") and path.endswith("/cancel"):
        parts = path.strip("/").split("/")
        try:
            reservation_id = int(parts[2])
        except (IndexError, TypeError, ValueError):
            reservation_id = None
        if reservation_id is not None:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT intervention_id FROM stock_reservations WHERE id = ?",
                (reservation_id,),
            )
            row = cursor.fetchone()
            conn.close()
            intervention_id = int(row[0]) if row and row[0] is not None else None

    # Un mouvement général éventuellement rattaché à une intervention.
    elif path == "/stock/mouvements/add":
        raw_id = request.form.get("intervention_id")
        if raw_id:
            try:
                intervention_id = int(raw_id)
            except (TypeError, ValueError):
                intervention_id = None

    if intervention_id is None:
        return None

    conn = get_db_connection()
    locked = _stock_intervention_is_locked(conn, intervention_id)
    conn.close()

    if locked:
        return redirect(f"/stock/intervention/{intervention_id}?locked=1")

    return None
# END STOCK_BEFORE_REPORT_WORKFLOW


# BEGIN MOBILE_PWA_SERVER
@app.after_request
def configure_mobile_pwa(response):
    # Le service worker est servi depuis /static, mais doit pouvoir contrôler
    # toute l'application. Ce header autorise explicitement le scope racine.
    if request.path == "/static/service-worker.js":
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
    return response
# END MOBILE_PWA_SERVER

# ==========================
# Lancement
# ==========================

if __name__ == "__main__":
    ensure_upload_dirs()
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
