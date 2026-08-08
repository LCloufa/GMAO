from pathlib import Path
import shutil
import sys

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_user_role_management.py")

SYNC_BEGIN = "# BEGIN USER_ROLE_SESSION_SYNC"
SYNC_END = "# END USER_ROLE_SESSION_SYNC"
ROUTE_BEGIN = "# BEGIN USER_ROLE_MANAGEMENT"
ROUTE_END = "# END USER_ROLE_MANAGEMENT"

OPERATOR_GUARD_ANCHOR = "# BEGIN OPERATOR_ACCESS_GUARD"
FALLBACK_SYNC_ANCHOR = "RYTHME_OPTIONS ="
ROUTE_ANCHOR = "# ==========================\n# Suppression compte"

SYNC_BLOCK = r'''
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

'''

ROUTE_BLOCK = r'''
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

'''


def insert_before(text: str, anchor: str, block: str) -> str:
    index = text.find(anchor)
    if index == -1:
        raise ValueError(f"Point d'insertion introuvable : {anchor}")
    return text[:index] + block + text[index:]


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")
    changed = False

    if SYNC_BEGIN not in text:
        sync_anchor = OPERATOR_GUARD_ANCHOR if OPERATOR_GUARD_ANCHOR in text else FALLBACK_SYNC_ANCHOR
        try:
            text = insert_before(text, sync_anchor, SYNC_BLOCK)
        except ValueError as exc:
            print(f"ERREUR : {exc}")
            return 1
        changed = True

    if ROUTE_BEGIN not in text:
        try:
            text = insert_before(text, ROUTE_ANCHOR, ROUTE_BLOCK)
        except ValueError as exc:
            print(f"ERREUR : {exc}")
            return 1
        changed = True

    if not changed:
        print("La gestion des rôles utilisateur est déjà installée dans app.py.")
        return 0

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    APP_PATH.write_text(text, encoding="utf-8")

    print("Gestion des rôles ajoutée à app.py.")
    print("admin : peut modifier uniquement les comptes non-admin")
    print("rôles attribuables : operator / technician")
    print("admin : rôle protégé et jamais attribuable depuis cette fonction")
    return 0


if __name__ == "__main__":
    sys.exit(main())
