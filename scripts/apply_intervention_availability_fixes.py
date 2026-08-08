from pathlib import Path
import re
import shutil
import sys


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_intervention_availability_fixes.py")
BEGIN = "# BEGIN REAL_AVAILABILITY_METRICS"
END = "# END REAL_AVAILABILITY_METRICS"


INDICATORS_BLOCK = r'''    # BEGIN REAL_AVAILABILITY_METRICS
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

'''


DELETE_TECHNICIEN = r'''@app.route("/techniciens/delete/<int:id>")
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
'''


DELETE_CLIENT = r'''@app.route("/clients/delete/<int:id>")
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
'''


def replace_function(text: str, route_literal: str, function_name: str, replacement: str) -> str:
    pattern = re.compile(
        rf'@app\.route\({re.escape(route_literal)}\)\s*\n'
        rf'(?:@[^\n]+\n)*'
        rf'def {re.escape(function_name)}\([^\n]*\):.*?'
        rf'(?=\n@app\.route\(|\n# ==========================|\Z)',
        re.DOTALL,
    )
    updated, count = pattern.subn(replacement.rstrip() + "\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"Impossible de remplacer {function_name} dans app.py")
    return updated


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        print("Les corrections interventions/disponibilité sont déjà installées.")
        return 0

    if "get_db_connection" not in text:
        print("ERREUR : ce patch est prévu pour le app.py PostgreSQL utilisant get_db_connection().")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    # Import du moteur de calcul réel.
    import_line = "from maintenance_metrics import calculate_availability_metrics\n"
    if import_line not in text:
        anchor = "from database_compat import get_db_connection\n"
        if anchor in text:
            text = text.replace(anchor, anchor + import_line, 1)
        else:
            text = import_line + text

    # Les interventions n'affichent que le code du technicien.
    text = text.replace("SELECT id, nom FROM techniciens WHERE statut='Actif'", "SELECT id, code FROM techniciens WHERE statut='Actif'")
    text = text.replace("SELECT id, nom FROM techniciens", "SELECT id, code FROM techniciens")
    text = text.replace("                   t.nom,\n", "                   t.code,\n")
    text = text.replace("               techniciens.nom\n", "               techniciens.code\n")
    text = text.replace("               techniciens.nom as technicien_nom\n", "               techniciens.code as technicien_code\n")

    # Suppressions sûres : aucun historique lié n'est effacé en cascade.
    text = replace_function(text, '"/techniciens/delete/<int:id>"', "delete_technicien", DELETE_TECHNICIEN)
    text = replace_function(text, '"/clients/delete/<int:id>"', "delete_client", DELETE_CLIENT)

    # Remplacement complet de l'ancien calcul basé sur estimated_duration.
    start_marker = "    # ==========================\n    # INDICATEURS MAINTENANCE\n"
    start = text.find(start_marker)
    if start == -1:
        raise RuntimeError("Bloc INDICATEURS MAINTENANCE introuvable")

    end_marker = "    conn.close()\n\n    return render_template("
    end = text.find(end_marker, start)
    if end == -1:
        raise RuntimeError("Fin du bloc indicateurs introuvable")

    text = text[:start] + INDICATORS_BLOCK + text[end:]

    APP_PATH.write_text(text, encoding="utf-8")

    print("Corrections installées dans app.py :")
    print("- suppressions client/technicien protégées")
    print("- techniciens affichés par code dans les interventions")
    print("- disponibilité basée sur la durée réelle et le rythme client")
    print("Aucune migration de schéma supplémentaire n'est nécessaire.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERREUR : {exc}")
        sys.exit(1)
