from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
BACKUP_PATH = ROOT / "app_before_intervention_report_pdf.py"
BEGIN = "# BEGIN INTERVENTION_REPORT_PDF_EXPORT"
END = "# END INTERVENTION_REPORT_PDF_EXPORT"

ROUTE = r'''
# BEGIN INTERVENTION_REPORT_PDF_EXPORT
@app.route("/rapports/<int:id>/pdf")
@login_required
def rapport_pdf(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT r.id,
               r.intervention_id,
               r.travaux,
               r.heure_debut,
               r.heure_fin,
               r.observations,
               r.etat,
               r.recommandations,
               r.created_at,
               COALESCE(u.username, '-'),
               i.title,
               i.type,
               i.priority,
               i.status,
               i.scheduled_date,
               i.scheduled_time,
               i.estimated_duration,
               e.id,
               e.nom,
               e.code,
               e.type,
               e.emplacement,
               e.numero_serie,
               e.fabricant,
               e.modele,
               c.nom,
               c.email,
               c.telephone,
               t.nom,
               t.prenom,
               t.code
        FROM rapports_intervention r
        LEFT JOIN interventions i ON i.id = r.intervention_id
        LEFT JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN clients c ON c.id = e.client_id
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        LEFT JOIN users u ON u.id = r.created_by_user_id
        WHERE r.id = ?
        """,
        (id,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Rapport introuvable", 404

    technician_name = " ".join(
        part for part in [row[29], row[28]] if part
    ).strip() or row[9] or "-"

    data = {
        "report_id": row[0],
        "intervention_id": row[1],
        "travaux": row[2],
        "heure_debut": row[3],
        "heure_fin": row[4],
        "observations": row[5],
        "etat": row[6],
        "recommandations": row[7],
        "created_at": row[8],
        "author": row[9],
        "intervention_title": row[10],
        "intervention_type": row[11],
        "priority": row[12],
        "intervention_status": row[13],
        "scheduled_date": row[14],
        "scheduled_time": row[15],
        "estimated_duration": row[16],
        "equipment_id": row[17],
        "equipment_name": row[18],
        "equipment_code": row[19],
        "equipment_type": row[20],
        "equipment_location": row[21],
        "serial_number": row[22],
        "manufacturer": row[23],
        "model": row[24],
        "client_name": row[25],
        "client_email": row[26],
        "client_phone": row[27],
        "technician_name": technician_name,
        "technician_code": row[30],
        "work_date": row[14],
    }

    materials = []
    cursor.execute("SELECT to_regclass('public.intervention_stock_items')")
    stock_table = cursor.fetchone()
    if stock_table and stock_table[0] and row[1]:
        cursor.execute(
            """
            SELECT a.designation,
                   a.reference,
                   isi.quantite_utilisee,
                   a.unite
            FROM intervention_stock_items isi
            LEFT JOIN stock_articles a ON a.id = isi.article_id
            WHERE isi.intervention_id = ?
            ORDER BY isi.created_at ASC, isi.id ASC
            """,
            (row[1],),
        )
        for material_row in cursor.fetchall():
            quantity = str(material_row[2] or "-")
            if material_row[3]:
                quantity = f"{quantity} {material_row[3]}"
            materials.append(
                {
                    "designation": material_row[0] or "-",
                    "reference": material_row[1] or "-",
                    "quantite": quantity,
                }
            )

    conn.close()
    output = create_intervention_report_pdf(data, materials=materials)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"Rapport_Intervention_{id}.pdf",
        mimetype="application/pdf",
    )
# END INTERVENTION_REPORT_PDF_EXPORT

'''


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")
    if BEGIN in text and END in text:
        print("L'export PDF des rapports d'intervention est déjà installé.")
        return 0

    if "get_db_connection" not in text:
        print("ERREUR : app.py ne semble pas être la version PostgreSQL attendue.")
        return 1

    import_line = "from intervention_report_pdf import create_intervention_report_pdf"
    if import_line not in text:
        anchor = "from database_compat import get_db_connection"
        pos = text.find(anchor)
        if pos == -1:
            print("ERREUR : import get_db_connection introuvable dans app.py.")
            return 1
        end = pos + len(anchor)
        text = text[:end] + "\n" + import_line + text[end:]

    route_anchor = '@app.route("/rapports/<int:id>/details")'
    route_pos = text.find(route_anchor)
    if route_pos == -1:
        print("ERREUR : route de détail des rapports introuvable dans app.py.")
        return 1

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    text = text[:route_pos] + ROUTE + text[route_pos:]
    APP_PATH.write_text(text, encoding="utf-8")

    print("Export PDF des rapports d'intervention installé dans app.py.")
    print("Route ajoutée : /rapports/<id>/pdf")
    print("Aucune modification du schéma PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
