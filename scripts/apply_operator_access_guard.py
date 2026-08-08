from pathlib import Path
import shutil
import sys

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_operator_guard.py")

BEGIN = "# BEGIN OPERATOR_ACCESS_GUARD"
END = "# END OPERATOR_ACCESS_GUARD"
ANCHOR = "RYTHME_OPTIONS ="

GUARD = r'''
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

'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("Le contrôle d'accès opérateur est déjà installé dans app.py.")
        return 0

    anchor_index = text.find(ANCHOR)
    if anchor_index == -1:
        print(f"ERREUR : point d'insertion '{ANCHOR}' introuvable dans app.py.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    patched = text[:anchor_index] + GUARD + text[anchor_index:]
    APP_PATH.write_text(patched, encoding="utf-8")

    print("Contrôle d'accès opérateur ajouté à app.py.")
    print("operator : dashboard + déclarations uniquement")
    print("technician/admin : accès inchangés")
    return 0


if __name__ == "__main__":
    sys.exit(main())
