from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
STOCK = ROOT / "templates" / "stock_intervention.html"
INTER = ROOT / "templates" / "interventions.html"

APP_OLD = '    session.modified = True\n    return {"ok": True}\n'
APP_NEW = '    session.modified = True\n    return redirect(f"/interventions?report={intervention_id}")\n'

BUTTON_OLD = '''        <button class="btn-success" style="width:100%;" type="button" onclick="validateStockAndOpenReport({{ intervention[0] }}, {{ ((intervention[4] or '')|string)|tojson }})">\n            ✓ Pièces vérifiées → Rédiger le rapport\n        </button>'''
BUTTON_NEW = '''        <form method="POST" action="/stock/intervention/{{ intervention[0] }}/validate" style="margin:0;">\n            <button class="btn-success" style="width:100%;" type="submit">\n                ✓ Pièces vérifiées → Rédiger le rapport\n            </button>\n        </form>'''

SCRIPT_START = '<script>\nasync function validateStockAndOpenReport('
SCRIPT_END = '</script>'
MARKER = '// STOCK_REPORT_AUTO_OPEN'
AUTO = '''\n<script>\n// STOCK_REPORT_AUTO_OPEN\ndocument.addEventListener("DOMContentLoaded", async () => {\n    const id = Number(new URLSearchParams(window.location.search).get("report") || 0);\n    if (!id) return;\n    try {\n        const r = await fetch(`/interventions/${id}/details`, {cache: "no-store"});\n        const data = await r.json();\n        document.getElementById("drawerTitle").innerText = "Rapport d'intervention";\n        openReportForm(id, data.scheduled_time || "");\n        document.getElementById("drawer").classList.add("open");\n        history.replaceState({}, "", "/interventions");\n    } catch (e) {\n        console.error(e);\n        alert("Les pièces sont validées, mais le formulaire du rapport n'a pas pu s'ouvrir.");\n    }\n});\n</script>\n'''


def backup(path, suffix):
    dest = path.with_name(path.stem + suffix + path.suffix)
    if not dest.exists():
        shutil.copy2(path, dest)
        print("Sauvegarde créée :", dest.name)


def main():
    for p in (APP, STOCK, INTER):
        if not p.exists():
            raise RuntimeError(f"Fichier introuvable : {p}")

    a = APP.read_text(encoding="utf-8")
    s = STOCK.read_text(encoding="utf-8")
    i = INTER.read_text(encoding="utf-8")

    if APP_OLD in a:
        backup(APP, "_before_stock_report_button_fix")
        a = a.replace(APP_OLD, APP_NEW, 1)
    elif '/interventions?report={intervention_id}' not in a:
        raise RuntimeError("Route validate attendue introuvable dans app.py")

    if BUTTON_OLD in s:
        backup(STOCK, "_before_report_button_fix")
        s = s.replace(BUTTON_OLD, BUTTON_NEW, 1)
    elif 'action="/stock/intervention/{{ intervention[0] }}/validate"' not in s:
        raise RuntimeError("Bouton de rapport attendu introuvable")

    pos = s.find(SCRIPT_START)
    if pos != -1:
        end = s.find(SCRIPT_END, pos)
        if end != -1:
            s = s[:pos] + s[end + len(SCRIPT_END):]

    if MARKER not in i:
        backup(INTER, "_before_report_auto_open")
        pos = i.rfind("{% endblock %}")
        if pos == -1:
            raise RuntimeError("Fin du template interventions introuvable")
        i = i[:pos] + AUTO + "\n" + i[pos:]

    APP.write_text(a, encoding="utf-8")
    STOCK.write_text(s, encoding="utf-8")
    INTER.write_text(i, encoding="utf-8")
    print("Correctif installé : validation pièces -> Interventions -> formulaire rapport.")
    print("Aucune migration PostgreSQL nécessaire.")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print("ERREUR :", exc)
        sys.exit(1)
