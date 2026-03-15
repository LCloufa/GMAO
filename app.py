from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from flask import send_file
from openpyxl import load_workbook
from tempfile import NamedTemporaryFile
import sqlite3
import os
from werkzeug.utils import secure_filename
import csv
from flask import Response
app = Flask(__name__)
app.secret_key = "cle_super_secrete_change_moi"
ADMIN_ACCESS_KEY = "GMAO-2026-SECURE"
OPERATOR_ACCESS_KEY = "GMAO-OP-2026"
TECH_ACCESS_KEY = "GMAO-TECH-2026"

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

# ==========================
# Base de données
# ==========================

def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'user'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        email TEXT,
        telephone TEXT,
        site_web TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS techniciens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        prenom TEXT NOT NULL,
        code TEXT NOT NULL,
        email TEXT,
        telephone TEXT,
        specialite TEXT,
        statut TEXT DEFAULT 'Actif'
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS equipements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        code TEXT,
        type TEXT,
        statut TEXT DEFAULT 'Opérationnel',
        emplacement TEXT,
        client_id INTEGER,
        fabricant TEXT,
        modele TEXT,
        numero_serie TEXT,
        date_installation TEXT,
        photo TEXT,
        FOREIGN KEY (client_id) REFERENCES clients(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS equipement_documents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipement_id INTEGER,
        filename TEXT,
        filepath TEXT,
        FOREIGN KEY (equipement_id) REFERENCES equipements(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS interventions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        equipment_id INTEGER NOT NULL,
        routine_id TEXT,
        type TEXT CHECK(type IN ('preventive','corrective','predictive','emergency')) NOT NULL,
        priority TEXT CHECK(priority IN ('low','medium','high','critical')) DEFAULT 'medium',
        status TEXT CHECK(status IN ('planned','in_progress','completed','cancelled','postponed')) DEFAULT 'planned',
        scheduled_date TEXT NOT NULL,
        scheduled_time TEXT,
        assigned_to INTEGER,
        estimated_duration INTEGER,
        description TEXT,
        completion_date TEXT,
        FOREIGN KEY (equipment_id) REFERENCES equipements(id),
        FOREIGN KEY (assigned_to) REFERENCES techniciens(id)
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS declarations_panne (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        equipment_id INTEGER NOT NULL,
        declared_by_user_id INTEGER,     -- opérateur (users.id)
        declared_by_name TEXT,           -- optionnel: nom saisi
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        urgency TEXT CHECK(urgency IN ('low','medium','high','critical')) DEFAULT 'medium',
        location TEXT,
        status TEXT CHECK(status IN ('pending','in_progress','resolved','rejected')) DEFAULT 'pending',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT,
        intervention_id INTEGER,         -- si une intervention a été créée depuis cette déclaration
        FOREIGN KEY (equipment_id) REFERENCES equipements(id),
        FOREIGN KEY (declared_by_user_id) REFERENCES users(id),
        FOREIGN KEY (intervention_id) REFERENCES interventions(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS declaration_photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        declaration_id INTEGER NOT NULL,
        filepath TEXT NOT NULL,
        FOREIGN KEY (declaration_id) REFERENCES declarations_panne(id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rapports_intervention (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        intervention_id INTEGER NOT NULL,
        travaux TEXT NOT NULL,
        heure_debut TEXT,
        heure_fin TEXT NOT NULL,
        observations TEXT,
        etat TEXT CHECK(etat IN ('Opérationnel','Nécessite un suivi','Toujours en panne')) NOT NULL,
        recommandations TEXT,
        created_by_user_id INTEGER,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT,
        FOREIGN KEY (intervention_id) REFERENCES interventions(id),
        FOREIGN KEY (created_by_user_id) REFERENCES users(id)
    )
    """)
    conn.commit()
    conn.close()


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

    next_statut = "En panne" if (has_active_declaration or has_active_intervention) else "Opérationnel"
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

        conn = sqlite3.connect("database.db")
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

        conn = sqlite3.connect("database.db")
        conn.row_factory = sqlite3.Row
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


    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id, username, role FROM users")
    users = cursor.fetchall()

    conn.close()
    return render_template("users.html", users=users)

@app.route("/debug-users")
@admin_required
def debug_users():
    conn = sqlite3.connect("database.db")
    cur = conn.cursor()
    cur.execute("SELECT id, username, role FROM users")
    users = cur.fetchall()
    conn.close()
    return str(users)
# ==========================
# Suppression compte
# ==========================

@app.route("/users/delete/<int:id>", methods=["POST"])
@admin_required
def delete_user(id):
    # Empêche de se supprimer soi-même
    if session.get("user_id") == id:
        return "Vous ne pouvez pas supprimer votre propre compte"

    conn = sqlite3.connect("database.db")
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
    conn = sqlite3.connect("database.db")
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
                   t.nom,
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
                   t.nom,
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

    from datetime import date, timedelta

    today = date.today()
    today_str = today.isoformat()
    soon_str = (today + timedelta(days=3)).isoformat()

    query = """
    SELECT 
        e.id,
        e.nom,
        c.nom,
        CASE
        WHEN EXISTS (
            SELECT 1 FROM interventions i
            WHERE i.equipment_id = e.id
            AND i.type = 'corrective'
            AND i.priority = 'critical'
            AND i.scheduled_date = ?
        )
        THEN 'En panne'
        WHEN EXISTS (
            SELECT 1 FROM interventions i
            WHERE i.equipment_id = e.id
            AND i.scheduled_date = ?
        )
        THEN 'Maintenance'
        WHEN EXISTS (
            SELECT 1 FROM interventions i
            WHERE i.equipment_id = e.id
            AND i.scheduled_date > ?
            AND i.scheduled_date <= ?
        )
        THEN 'Planifiée'
        ELSE 'Opérationnel'
        END as etat
    FROM equipements e
    LEFT JOIN clients c ON e.client_id = c.id
    """

    params = [today_str, today_str, today_str, soon_str]

    if selected_client:
        query += " WHERE e.client_id = ?"
        params.append(selected_client)

    cursor.execute(query, params)
    equipements_etat = cursor.fetchall()

    maintenance_today = sum(
        1 for e in equipements_etat
        if e[3] in ("Maintenance", "En panne")
    )

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
        selected_client=selected_client
    )
# ==========================
# ÉQUIPEMENTS
# ==========================

@app.route("/equipements")
@login_required
def equipements():
    recherche = request.args.get("q", "").strip()
    client_filtre = request.args.get("client_id", "").strip()

    conn = sqlite3.connect("database.db")
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
    localisation = request.form["localisation"]
    client_id = request.form["client_id"]

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO equipements (nom, type, numero_serie, emplacement, client_id)
        VALUES (?, ?, ?, ?, ?)
    """, (nom, type_eq, numero_serie, emplacement, client_id))

    conn.commit()
    conn.close()

    return redirect("/equipements")

@app.route("/equipements/delete/<int:id>")
def delete_equipement(id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM equipements WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect("/equipements")

@app.route("/modifier_equipement/<int:id>", methods=["GET", "POST"])
@login_required
def modifier_equipement(id):

    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
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

    conn = sqlite3.connect("database.db")
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

    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
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

    conn = sqlite3.connect("database.db")
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
    conn = sqlite3.connect("database.db")
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

    conn = sqlite3.connect("database.db")
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
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM techniciens WHERE id = ?", (id,))

    conn.commit()
    conn.close()

    return redirect("/techniciens")


@app.route("/modifier_technicien/<int:id>", methods=["GET", "POST"])
def modifier_technicien(id):
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
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
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id, nom FROM equipements")
    equipements = cursor.fetchall()

    cursor.execute("SELECT id, nom FROM techniciens")
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
               techniciens.nom
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
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id, nom FROM equipements")
    equipements = cursor.fetchall()

    cursor.execute("SELECT id, nom FROM techniciens WHERE statut='Actif'")
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

    conn = sqlite3.connect("database.db")
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
    conn = sqlite3.connect("database.db")
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
    conn = sqlite3.connect("database.db")
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

    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT interventions.*,
               equipements.nom as equipement_nom,
               techniciens.nom as technicien_nom
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

    conn = sqlite3.connect("database.db")
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


@app.route("/rapports/<int:id>/details")
@login_required
def rapport_details(id):
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
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

    conn = sqlite3.connect("database.db")
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
    if data.get("etat") == "Toujours en panne":
        cursor.execute("UPDATE equipements SET statut='En panne' WHERE id=?", (equipement_id,))
    else:
        sync_equipement_statut(conn, equipement_id)

    conn.commit()
    conn.close()
    return redirect("/rapports")


@app.route("/rapports/<int:id>/delete")
@login_required
@role_required("admin")
def delete_rapport(id):
    conn = sqlite3.connect("database.db")
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
    conn = sqlite3.connect("database.db")
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

    conn = sqlite3.connect("database.db")
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
    
@app.route("/declarations/nouvelle", methods=["GET", "POST"])
@login_required
@role_required("operator", "admin", "technician")  # un tech peut aussi déclarer si besoin
def nouvelle_declaration():

    conn = sqlite3.connect("database.db")
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

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("SELECT equipment_id FROM declarations_panne WHERE id=?", (id,))
    row = cursor.fetchone()

    cursor.execute("""
        UPDATE declarations_panne
        SET status=?, updated_at=datetime('now')
        WHERE id=?
    """, (status, id))

    if row:
        sync_equipement_statut(conn, row[0])

    conn.commit()
    conn.close()
    return redirect("/declarations")


@app.route("/declarations/<int:id>/create_intervention", methods=["GET", "POST"])
@login_required
@role_required("technician", "admin")
def declaration_create_intervention(id):

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    # Récup déclaration
    cursor.execute("""
        SELECT d.id, d.title, d.description, d.urgency, d.location, d.equipment_id,
               e.nom, e.code
        FROM declarations_panne d
        LEFT JOIN equipements e ON d.equipment_id = e.id
        WHERE d.id=?
    """, (id,))
    dec = cursor.fetchone()
    if not dec:
        conn.close()
        return "Déclaration introuvable", 404

    cursor.execute("SELECT id, nom FROM techniciens WHERE statut='Actif'")
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
    
@app.route("/export/interventions")
@login_required
def export_interventions():

    conn = sqlite3.connect("database.db")
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
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT clients.id,
               clients.nom,
               clients.email,
               clients.telephone,
               COALESCE(SUM(interventions.estimated_duration), 0)
        FROM clients
        LEFT JOIN equipements ON equipements.client_id = clients.id
        LEFT JOIN interventions ON interventions.equipment_id = equipements.id
        GROUP BY clients.id
    """)

    clients = cursor.fetchall()
    conn.close()

    return render_template("clients.html", clients=clients)

@app.route("/clients/add", methods=["POST"])
def add_client():
    nom = request.form.get("nom")
    email = request.form.get("email")
    telephone = request.form.get("telephone")
    site_web = request.form.get("site_web")

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO clients (nom, email, telephone, site_web)
        VALUES (?, ?, ?, ?)
    """, (nom, email, telephone, site_web))

    conn.commit()
    conn.close()

    return redirect("/clients")

@app.route("/clients/delete/<int:id>")
def delete_client(id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM clients WHERE id=?", (id,))

    conn.commit()
    conn.close()

    return redirect("/clients")

@app.route("/modifier_client/<int:id>", methods=["GET", "POST"])
def modifier_client(id):
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == "POST":
        nom = request.form["nom"]
        email = request.form["email"]
        telephone = request.form["telephone"]
        site_web = request.form.get("site_web")


        cursor.execute("""
            UPDATE clients
            SET nom=?, email=?, telephone=?, site_web=?
            WHERE id=?
        """, (nom, email, telephone, site_web, id))

        conn.commit()
        conn.close()
        return redirect("/clients")

    cursor.execute("SELECT * FROM clients WHERE id=?", (id,))
    client = cursor.fetchone()
    conn.close()

    return render_template("modifier_client.html", client=client)
@app.route("/export/clients")
@login_required
def export_clients():

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT clients.nom,
               clients.email,
               clients.telephone,
               COALESCE(SUM(interventions.estimated_duration),0)
        FROM clients
        LEFT JOIN equipements ON equipements.client_id = clients.id
        LEFT JOIN interventions ON interventions.equipment_id = equipements.id
        GROUP BY clients.id
    """)

    rows = cursor.fetchall()
    conn.close()

    def generate():
        yield "Nom,Email,Telephone,Heures_totales\n"
        for r in rows:
            heures = round(r[3] / 60, 2)
            yield f"{r[0]},{r[1]},{r[2]},{heures}\n"

    return Response(generate(),
        mimetype="text/csv",
        headers={"Content-Disposition":"attachment;filename=clients.csv"})
# ==========================
# Export
# ==========================
@app.route("/export/gmao-xlsx")
@login_required
def export_gmao_xlsx():

    # ======================
    # 1) DATA depuis SQLite
    # ======================
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("SELECT id, nom FROM clients")
    clients = cursor.fetchall()
    client_name_by_id = {cid: nom for cid, nom in clients}

    cursor.execute("""
        SELECT client_id, nom, code, type, numero_serie, emplacement
        FROM equipements
        ORDER BY client_id, nom
    """)
    equipements = cursor.fetchall()

    equip_by_client = {}
    for client_id, nom, code, typ, serie, empl in equipements:
        equip_by_client.setdefault(client_id, []).append((nom, code, typ, serie, empl))

    cursor.execute("""
        SELECT clients.id,
               COALESCE(SUM(interventions.estimated_duration), 0) as total_minutes
        FROM clients
        LEFT JOIN equipements ON equipements.client_id = clients.id
        LEFT JOIN interventions ON interventions.equipment_id = equipements.id
        GROUP BY clients.id
    """)
    minutes_by_client = dict(cursor.fetchall())
    hours_by_client = {cid: round((minutes_by_client.get(cid, 0) or 0) / 60, 2) for cid, _ in clients}

    conn.close()

    # ======================
    # 2) Charger le modèle
    # ======================
    wb = load_workbook("GMAO.xlsx")

    def get_sheet(name_candidates):
        lower_map = {s.lower(): s for s in wb.sheetnames}
        for n in name_candidates:
            if n.lower() in lower_map:
                return wb[lower_map[n.lower()]]
        return None

    ws_eq = get_sheet(["Listing équip", "Listing equip", "Listing équipement", "Listing equipement"])
    ws_h  = get_sheet(["Listing heures", "Listing heure"])
    ws_i  = get_sheet(["Listing inter", "Listing intervention", "Listing interventions"])
    if not ws_eq:
        return "Onglet introuvable: Listing équip", 500
    if not ws_h:
        return "Onglet introuvable: Listing heures", 500
    if not ws_i:
        return "Onglet introuvable: Listing intervention", 500

    # ======================
    # 3) Mapping couleur -> client
    # ======================
    # ⚠️ Les codes RGB exacts peuvent varier selon Excel.
    # Si ça ne matche pas du 1er coup, je te dis comment récupérer la bonne valeur.
    COLOR_TO_CLIENT = {
        "FF00B050": "ROGA MECANIQUE",  # vert
        "FF0070C0": "GALY AERO",       # bleu
        "FF7030A0": "GALY CND",        # violet
    }

    def cell_rgb(cell):
        try:
            c = cell.fill.fgColor
            return c.rgb if c and c.type == "rgb" else None
        except:
            return None

    # ======================
    # 4) Remplir “listing équipement” (par blocs de colonnes)
    # ======================
    if ws_eq:
        BLOCKS = [
            ("B", "F", "ROGA MECANIQUE"),
            ("H", "L", "GALY AERO"),
            ("N", "R", "GALY CND"),
        ]

        def col_letter_to_index(letter: str) -> int:
            return ord(letter.upper()) - ord("A") + 1

        def find_header_row_in_block(ws, start_col, end_col, max_scan_rows=50):
            # on cherche une ligne qui contient au moins 2 libellés connus
            wanted = {"nom", "code", "type", "emplacement", "localisation", "n°série", "n° serie", "numero_serie", "numéro de série", "nserie", "n°serie"}
            for r in range(1, max_scan_rows + 1):
                hits = 0
                for c in range(start_col, end_col + 1):
                    v = str(ws.cell(r, c).value or "").strip().lower()
                    if v in wanted or "série" in v or "serie" in v:
                        hits += 1
                if hits >= 2:
                    return r
            return None

        def build_col_map(ws, header_row, start_col, end_col):
            col_map = {}
            for c in range(start_col, end_col + 1):
                t = str(ws.cell(header_row, c).value or "").strip().lower()
                if t in ("nom", "désignation", "designation"):
                    col_map["nom"] = c
                elif t == "code":
                    col_map["code"] = c
                elif t == "type":
                    col_map["type"] = c
                elif "série" in t or "serie" in t:
                    col_map["serie"] = c
                elif "emplacement" in t or "localisation" in t:
                    col_map["empl"] = c
            return col_map

        # récupérer l’ID client correspondant au nom
        def get_client_id_by_name(client_name: str):
            for cid, cname in client_name_by_id.items():
                if (cname or "").strip().lower() == client_name.strip().lower():
                    return cid
            return None

        for startL, endL, client_name in BLOCKS:
            start_col = col_letter_to_index(startL)
            end_col = col_letter_to_index(endL)

            header_row = find_header_row_in_block(ws_eq, start_col, end_col)
            if not header_row:
                continue

            col_map = build_col_map(ws_eq, header_row, start_col, end_col)
            data_start = header_row + 1

            client_id = get_client_id_by_name(client_name)
            rows_to_write = equip_by_client.get(client_id, []) if client_id else []

            # Nettoyage (sans casser la mise en forme)
            for r in range(data_start, data_start + 200):
                for key, c in col_map.items():
                    ws_eq.cell(r, c).value = None

            # Remplissage
            r = data_start
            for (nom, code, typ, serie, empl) in rows_to_write:
                if "nom" in col_map:   ws_eq.cell(r, col_map["nom"]).value = nom
                if "code" in col_map:  ws_eq.cell(r, col_map["code"]).value = code
                if "type" in col_map:  ws_eq.cell(r, col_map["type"]).value = typ
                if "serie" in col_map: ws_eq.cell(r, col_map["serie"]).value = serie
                if "empl" in col_map:  ws_eq.cell(r, col_map["empl"]).value = empl
                r += 1
    # ======================
    # 5) Remplir “listing heures”
    # ======================
    if ws_h:
        # On cherche les lignes où le nom du client apparaît, puis on remplit la cellule à droite
        for row in range(1, ws_h.max_row + 1):
            for col in range(1, ws_h.max_column + 1):
                v = str(ws_h.cell(row, col).value or "").strip().lower()
                for cid, cname in client_name_by_id.items():
                    if v == cname.strip().lower():
                        # hypothèse : la colonne des heures est juste à droite
                        ws_h.cell(row, col + 1).value = hours_by_client.get(cid, 0.0)

    # ======================
    # 6) Retour fichier
    # ======================
    tmp = NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()

    return send_file(tmp.name, as_attachment=True, download_name="Export_GMAO.xlsx")
# ==========================
# Lancement
# ==========================

if __name__ == "__main__":
    ensure_upload_dirs()
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))












