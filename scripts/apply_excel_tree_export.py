from pathlib import Path
import shutil
import sys


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BACKUP_PATH = APP_PATH.with_name("app_before_excel_tree_export.py")
BEGIN = "# BEGIN EXCEL_TREE_EXPORT"
END = "# END EXCEL_TREE_EXPORT"


def main() -> int:
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} introuvable")
        return 1

    text = APP_PATH.read_text(encoding="utf-8")

    if BEGIN in text and END in text:
        print("L'export Excel en arborescence est déjà installé dans app.py.")
        return 0

    if "get_db_connection" not in text:
        print("ERREUR : app.py ne semble pas être la version PostgreSQL attendue.")
        return 1

    route_start = text.find('@app.route("/export/gmao-xlsx")')
    if route_start == -1:
        print("ERREUR : route /export/gmao-xlsx introuvable dans app.py.")
        return 1

    launch_marker = "# ==========================\n# Lancement"
    route_end = text.find(launch_marker, route_start)
    if route_end == -1:
        print("ERREUR : fin de la route d'export introuvable dans app.py.")
        return 1

    import_line = "from excel_export import create_gmao_excel_export"
    if import_line not in text:
        anchor = "from database_compat import get_db_connection"
        anchor_pos = text.find(anchor)
        if anchor_pos == -1:
            print("ERREUR : import get_db_connection introuvable dans app.py.")
            return 1
        anchor_end = anchor_pos + len(anchor)
        text = text[:anchor_end] + "\n" + import_line + text[anchor_end:]
        route_start = text.find('@app.route("/export/gmao-xlsx")')
        route_end = text.find(launch_marker, route_start)

    new_route = f'''{BEGIN}
@app.route("/export/gmao-xlsx")
@login_required
def export_gmao_xlsx():
    template_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "GMAO.xlsx",
    )
    if not os.path.isfile(template_path):
        return "Modèle Excel GMAO.xlsx introuvable dans le dossier de l'application.", 500

    output = create_gmao_excel_export(get_db_connection, template_path)
    return send_file(
        output,
        as_attachment=True,
        download_name="Export_GMAO.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
{END}

'''

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH.name}")

    text = text[:route_start] + new_route + text[route_end:]
    APP_PATH.write_text(text, encoding="utf-8")

    print("Export Excel en arborescence installé dans app.py.")
    print("Le bouton du dashboard conserve la route /export/gmao-xlsx.")
    print("Aucune modification de schéma PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
