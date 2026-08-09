from pathlib import Path
import shutil
import sys


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_report_completion_integrity.py")
BEGIN = "# BEGIN REPORT_COMPLETION_INTEGRITY"
END = "# END REPORT_COMPLETION_INTEGRITY"


BLOCK = r'''
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

'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("La protection anti-double-rapport est déjà installée dans app.py.")
        return 0

    if "get_db_connection" not in text:
        print("ERREUR : ce patch est prévu pour le app.py PostgreSQL utilisant get_db_connection().")
        return 1

    launch_markers = [
        "# ==========================\n# Lancement",
        "if __name__ == \"__main__\":",
    ]
    insert_at = -1
    for marker in launch_markers:
        pos = text.find(marker)
        if pos != -1:
            insert_at = pos
            break

    if insert_at == -1:
        print("ERREUR : point d'insertion avant le lancement de Flask introuvable.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    text = text[:insert_at] + BLOCK + text[insert_at:]
    APP_PATH.write_text(text, encoding="utf-8")

    print("Protection anti-double-rapport installée dans app.py.")
    print("Une intervention ne peut désormais recevoir qu'un seul rapport côté serveur.")
    print("Aucune migration PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERREUR : {exc}")
        sys.exit(1)
