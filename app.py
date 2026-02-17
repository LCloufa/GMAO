from flask import Flask, render_template, request, redirect
import sqlite3

app = Flask(__name__)

# ==========================
# Base de données
# ==========================

def init_db():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        email TEXT,
        telephone TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS techniciens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        specialite TEXT,
        statut TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS equipements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        type TEXT,
        numero_serie TEXT,
        localisation TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS interventions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT,
        statut TEXT,
        commentaire TEXT,
        equipement_id INTEGER,
        technicien_id INTEGER,
        FOREIGN KEY (equipement_id) REFERENCES equipements(id),
        FOREIGN KEY (technicien_id) REFERENCES techniciens(id)
    )
    """)

    conn.commit()
    conn.close()

# ==========================
# Dashboard
# ==========================

@app.route("/")
def dashboard():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM equipements")
    nb_equipements = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM interventions WHERE statut='En cours'")
    en_cours = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM interventions WHERE statut='Planifiée'")
    planifiees = cursor.fetchone()[0]

    conn.close()

    return render_template("dashboard.html",
                           nb_equipements=nb_equipements,
                           en_cours=en_cours,
                           planifiees=planifiees)

# ==========================
# ÉQUIPEMENTS
# ==========================

@app.route("/equipements")
def equipements():
    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, nom, type, numero_serie, localisation
        FROM equipements
    """)

    equipements = cursor.fetchall()
    conn.close()

    return render_template("equipements.html", equipements=equipements)

@app.route("/equipements/add", methods=["POST"])
def add_equipement():
    nom = request.form["nom"]
    type_eq = request.form["type"]
    numero_serie = request.form["numero_serie"]
    localisation = request.form["localisation"]

    conn = sqlite3.connect("database.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO equipements (nom, type, numero_serie, localisation)
        VALUES (?, ?, ?, ?)
    """, (nom, type_eq, numero_serie, localisation))

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
def modifier_equipement(id):
    conn = sqlite3.connect("database.db")
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if request.method == "POST":
        nom = request.form["nom"]
        numero_serie = request.form["numero_serie"]
        localisation = request.form["localisation"]

        cursor.execute("""
            UPDATE equipements
            SET nom=?, numero_serie=?, localisation=?
            WHERE id=?
        """, (nom, numero_serie, localisation, id))

        conn.commit()
        conn.close()
        return redirect("/equipements")

    cursor.execute("SELECT * FROM equipements WHERE id=?", (id,))
    equipement = cursor.fetchone()
    conn.close()

    return render_template("modifier_equipement.html", equipement=equipement)

# ==========================
# Lancement
# ==========================

if __name__ == "__main__":
    init_db()
    app.run(debug=True)
