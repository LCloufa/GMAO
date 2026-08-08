from pathlib import Path
import shutil
import sys

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_technician_onboarding.py")

BEGIN = "# BEGIN TECHNICIAN_ONBOARDING"
END = "# END TECHNICIAN_ONBOARDING"
ANCHOR = "RYTHME_OPTIONS ="

PATCH = r'''
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
    cursor.execute("SELECT id FROM techniciens WHERE user_id = ? LIMIT 1", (user_id,))
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
    cursor.execute("SELECT id FROM techniciens WHERE user_id = ? LIMIT 1", (user_id,))
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
            (user_id, nom, prenom, code, specialite, statut)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, nom, prenom, code, specialite or None, statut),
        )
        conn.commit()
        conn.close()

        return redirect("/")

    conn.close()
    return render_template("technicien_onboarding.html")
# END TECHNICIAN_ONBOARDING

'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("L'onboarding technicien est déjà installé dans app.py.")
        return 0

    if "get_db_connection" not in text:
        print("ERREUR : app.py n'utilise pas get_db_connection(). Patch interrompu.")
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

    print("Onboarding technicien ajouté à app.py.")
    print("IMPORTANT : appliquez ensuite la migration Flask-Migrate ajoutant techniciens.user_id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
