from pathlib import Path
import shutil
import sys


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_selective_database_reset.py")

BEGIN = "# BEGIN SELECTIVE_DATABASE_RESET"
END = "# END SELECTIVE_DATABASE_RESET"
ANCHOR = "# ==========================\n# Dashboard"

PATCH = r'''
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

'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("La réinitialisation sélective admin est déjà installée dans app.py.")
        return 0

    required_tokens = ("get_db_connection", "admin_required", "sync_equipement_statut")
    missing = [token for token in required_tokens if token not in text]
    if missing:
        print("ERREUR : app.py ne contient pas les éléments attendus : " + ", ".join(missing))
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

    print("Réinitialisation sélective admin ajoutée à app.py.")
    print("Choix disponibles : équipements, interventions, rapports, déclarations de panne.")
    print("Aucune migration de schéma supplémentaire n'est nécessaire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
