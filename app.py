from flask import Flask, render_template, request, redirect, session, url_for
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3
import os
from werkzeug.utils import secure_filename
app = Flask(__name__)
app.secret_key = "cle_super_secrete_change_moi"
ADMIN_ACCESS_KEY = "GMAO-2026-SECURE"

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

    conn.commit()
    conn.close()
# ==========================
# Inscription/Connexion/Déconnexion
# ==========================

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])
        access_key = request.form.get("access_key")

        role = "user"

        # Si clé admin valide
        if access_key == ADMIN_ACCESS_KEY:
            role = "admin"

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
    interventions = cursor.fetchall()

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
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT equipements.id,
               equipements.nom,
               equipements.type,
               equipements.numero_serie,
               equipements.emplacement,
               clients.nom
        FROM equipements
        LEFT JOIN clients ON equipements.client_id = clients.id
    """)
    equipements = cursor.fetchall()

    cursor.execute("SELECT id, nom FROM clients")

    clients = cursor.fetchall()

    conn.close()

    return render_template(
        "equipements.html",
        equipements=equipements,
        clients=clients
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
        INSERT INTO equipements (nom, type, numero_serie, localisation, client_id)
        VALUES (?, ?, ?, ?, ?)
    """, (nom, type_eq, numero_serie, localisation, client_id))

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

    conn.commit()
    conn.close()

    return redirect("/interventions")
@app.route("/interventions/delete/<int:id>")
def delete_intervention(id):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM interventions WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return redirect("/interventions")

@app.route("/interventions/update_status/<int:id>/<status>")
def update_status(id, status):
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

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
    
# ==========================
# Lancement
# ==========================

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))






