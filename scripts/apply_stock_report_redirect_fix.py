from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_stock_report_redirect_fix.py")
BEGIN = "# BEGIN STOCK_REPORT_REDIRECT_FIX"
END = "# END STOCK_REPORT_REDIRECT_FIX"

OLD = '''    session["stock_reviewed_interventions"] = reviewed[-100:]\n    session.modified = True\n    return {"ok": True}\n'''

NEW = '''    session["stock_reviewed_interventions"] = reviewed[-100:]\n    session.modified = True\n    # BEGIN STOCK_REPORT_REDIRECT_FIX\n    return redirect(f"/interventions?report={intervention_id}")\n    # END STOCK_REPORT_REDIRECT_FIX\n'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("Le correctif de redirection vers le rapport est déjà installé.")
        return 0

    if "# BEGIN STOCK_BEFORE_REPORT_WORKFLOW" not in text:
        print("ERREUR : le workflow pièces avant rapport n'est pas installé dans app.py.")
        return 1

    if OLD not in text:
        print("ERREUR : la fin de validate_stock_before_report attendue est introuvable.")
        print("Aucune modification n'a été effectuée.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    text = text.replace(OLD, NEW, 1)
    APP_PATH.write_text(text, encoding="utf-8")

    print("Correctif installé : après validation des pièces, retour vers Interventions et ouverture du rapport.")
    print("Aucune migration PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERREUR : {exc}")
        sys.exit(1)
