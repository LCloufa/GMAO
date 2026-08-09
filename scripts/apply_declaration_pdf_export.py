from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
TEMPLATE_PATH = ROOT / "templates" / "declarations.html"
APP_BACKUP = ROOT / "app_before_declaration_pdf_export.py"
TEMPLATE_BACKUP = ROOT / "templates" / "declarations_before_pdf_export.html"
APP_BEGIN = "# BEGIN DECLARATION_PDF_EXPORT"
APP_END = "# END DECLARATION_PDF_EXPORT"
TEMPLATE_MARKER = "<!-- DECLARATION_PDF_EXPORT_BUTTON -->"


ROUTE_BLOCK = r'''# BEGIN DECLARATION_PDF_EXPORT
@app.route("/declarations/<int:id>/pdf")
@login_required
def export_declaration_pdf(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT d.id,
               d.title,
               d.description,
               d.urgency,
               d.location,
               d.status,
               d.created_at,
               d.declared_by_name,
               e.nom,
               e.code,
               e.type,
               e.emplacement,
               e.numero_serie,
               e.fabricant,
               e.modele,
               c.nom,
               u.username,
               d.intervention_id,
               i.title
        FROM declarations_panne d
        LEFT JOIN equipements e ON e.id = d.equipment_id
        LEFT JOIN clients c ON c.id = e.client_id
        LEFT JOIN users u ON u.id = d.declared_by_user_id
        LEFT JOIN interventions i ON i.id = d.intervention_id
        WHERE d.id = ?
        """,
        (id,),
    )
    row = cursor.fetchone()
    if not row:
        conn.close()
        return "Déclaration introuvable", 404

    cursor.execute(
        "SELECT filepath FROM declaration_photos WHERE declaration_id = ? ORDER BY id ASC",
        (id,),
    )
    photo_paths = [photo[0] for photo in cursor.fetchall() if photo and photo[0]]
    conn.close()

    data = {
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "urgency": row[3],
        "location": row[4],
        "status": row[5],
        "created_at": row[6],
        "declared_by_name": row[7],
        "equipement_nom": row[8],
        "equipement_code": row[9],
        "equipement_type": row[10],
        "equipement_emplacement": row[11],
        "numero_serie": row[12],
        "fabricant": row[13],
        "modele": row[14],
        "client_nom": row[15],
        "username": row[16],
        "intervention_id": row[17],
        "intervention_title": row[18],
    }

    pdf_file = create_declaration_pdf(
        data,
        photo_paths=photo_paths,
        base_dir=os.path.dirname(os.path.abspath(__file__)),
    )
    return send_file(
        pdf_file,
        as_attachment=True,
        download_name=f"Declaration_panne_{id}.pdf",
        mimetype="application/pdf",
    )
# END DECLARATION_PDF_EXPORT

'''


BUTTON_HTML = '''      <!-- DECLARATION_PDF_EXPORT_BUTTON -->
      <div style="margin-top:10px;">
        <a class="btn-secondary"
           style="text-decoration:none; display:inline-flex; align-items:center; gap:6px;"
           href="/declarations/{{ d[0] }}/pdf">
          ⬇ PDF
        </a>
      </div>

'''


def patch_app() -> bool:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return False

    text = APP_PATH.read_text(encoding="utf-8")
    if "get_db_connection" not in text:
        print("ERREUR : app.py ne semble pas être la version PostgreSQL attendue.")
        return False

    if "from declaration_pdf import create_declaration_pdf" not in text:
        anchor = "from database_compat import get_db_connection"
        pos = text.find(anchor)
        if pos == -1:
            print("ERREUR : import get_db_connection introuvable dans app.py.")
            return False
        end = pos + len(anchor)
        text = text[:end] + "\nfrom declaration_pdf import create_declaration_pdf" + text[end:]

    if APP_BEGIN not in text:
        anchor = '@app.route("/declarations/nouvelle", methods=["GET", "POST"])'
        pos = text.find(anchor)
        if pos == -1:
            print("ERREUR : route /declarations/nouvelle introuvable dans app.py.")
            return False
        text = text[:pos] + ROUTE_BLOCK + text[pos:]

    if not APP_BACKUP.exists():
        shutil.copy2(APP_PATH, APP_BACKUP)
        print(f"Sauvegarde créée : {APP_BACKUP.name}")

    APP_PATH.write_text(text, encoding="utf-8")
    print("Route PDF des déclarations installée dans app.py.")
    return True


def patch_template() -> bool:
    if not TEMPLATE_PATH.exists():
        print(f"ERREUR : {TEMPLATE_PATH} introuvable")
        return False

    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    if TEMPLATE_MARKER in text:
        print("Bouton PDF déjà présent dans declarations.html.")
        return True

    anchor = '      <div style="margin-top:10px; color:#94a3b8; font-size:13px;">{{ d[6] }}</div>\n\n'
    if anchor not in text:
        print("ERREUR : emplacement du bouton PDF introuvable dans declarations.html.")
        return False

    if not TEMPLATE_BACKUP.exists():
        shutil.copy2(TEMPLATE_PATH, TEMPLATE_BACKUP)
        print(f"Sauvegarde créée : {TEMPLATE_BACKUP.name}")

    text = text.replace(anchor, anchor + BUTTON_HTML, 1)
    TEMPLATE_PATH.write_text(text, encoding="utf-8")
    print("Bouton PDF ajouté à chaque déclaration.")
    return True


def main() -> int:
    app_ok = patch_app()
    template_ok = patch_template()

    if not (app_ok and template_ok):
        return 1

    print("Export PDF des déclarations installé.")
    print("Aucune migration PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
