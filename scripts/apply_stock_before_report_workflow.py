from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
BASE_PATH = ROOT / "templates" / "base.html"
APP_BACKUP = APP_PATH.with_name("app_before_stock_before_report_workflow.py")
BASE_BACKUP = BASE_PATH.with_name("base_before_stock_before_report_workflow.html")
BEGIN = "# BEGIN STOCK_BEFORE_REPORT_WORKFLOW"
END = "# END STOCK_BEFORE_REPORT_WORKFLOW"


BACKEND_BLOCK = r'''
# BEGIN STOCK_BEFORE_REPORT_WORKFLOW

def _stock_intervention_is_locked(conn, intervention_id):
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM interventions WHERE id = ?", (intervention_id,))
    row = cursor.fetchone()
    if not row:
        return None

    status = str(row[0] or "").lower()
    cursor.execute(
        "SELECT COUNT(*) FROM rapports_intervention WHERE intervention_id = ?",
        (intervention_id,),
    )
    has_report = int(cursor.fetchone()[0] or 0) > 0
    return status == "completed" or has_report


def _stock_intervention_has_open_reservations(conn, intervention_id):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM stock_reservations
        WHERE intervention_id = ? AND statut = 'reserved'
        """,
        (intervention_id,),
    )
    return int(cursor.fetchone()[0] or 0) > 0


@app.route("/stock/intervention/<int:intervention_id>/validate", methods=["POST"])
@login_required
@role_required("admin", "technician")
def validate_stock_before_report(intervention_id):
    conn = get_db_connection()
    if not _stock_can_manage_intervention(conn, intervention_id):
        conn.close()
        return "Accès refusé à cette intervention.", 403

    locked = _stock_intervention_is_locked(conn, intervention_id)
    if locked is None:
        conn.close()
        return "Intervention introuvable.", 404
    if locked:
        conn.close()
        return "Le rapport a déjà été soumis : les pièces sont verrouillées.", 409
    if _stock_intervention_has_open_reservations(conn, intervention_id):
        conn.close()
        return "Il reste une ou plusieurs réservations ouvertes. Consomme-les ou annule-les avant de rédiger le rapport.", 409
    conn.close()

    reviewed = list(session.get("stock_reviewed_interventions", []))
    intervention_id = int(intervention_id)
    if intervention_id not in reviewed:
        reviewed.append(intervention_id)
    session["stock_reviewed_interventions"] = reviewed[-100:]
    session.modified = True
    return {"ok": True}


@app.before_request
def enforce_stock_before_report_workflow():
    if request.method != "POST":
        return None

    path = request.path

    # Le rapport ne peut être envoyé qu'après validation explicite de l'écran pièces.
    if path == "/rapports/add":
        raw_id = request.form.get("intervention_id")
        try:
            intervention_id = int(raw_id)
        except (TypeError, ValueError):
            return None

        reviewed = {int(value) for value in session.get("stock_reviewed_interventions", [])}
        if intervention_id not in reviewed:
            return (
                "Les pièces de cette intervention doivent être vérifiées avant de soumettre le rapport. "
                "Passe d'abord par 'Gérer les pièces'.",
                409,
            )

        conn = get_db_connection()
        if _stock_intervention_has_open_reservations(conn, intervention_id):
            conn.close()
            return (
                "Il reste une ou plusieurs réservations ouvertes. "
                "Consomme-les ou annule-les avant de soumettre le rapport.",
                409,
            )
        conn.close()
        return None

    # La validation de l'étape pièces doit naturellement rester autorisée.
    if path.startswith("/stock/intervention/") and path.endswith("/validate"):
        return None

    intervention_id = None

    # Réserver / consommer / retourner depuis une intervention.
    if path.startswith("/stock/intervention/"):
        parts = path.strip("/").split("/")
        if len(parts) >= 4:
            try:
                intervention_id = int(parts[2])
            except (TypeError, ValueError):
                intervention_id = None

    # Annulation d'une réservation.
    elif path.startswith("/stock/reservations/") and path.endswith("/cancel"):
        parts = path.strip("/").split("/")
        try:
            reservation_id = int(parts[2])
        except (IndexError, TypeError, ValueError):
            reservation_id = None
        if reservation_id is not None:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT intervention_id FROM stock_reservations WHERE id = ?",
                (reservation_id,),
            )
            row = cursor.fetchone()
            conn.close()
            intervention_id = int(row[0]) if row and row[0] is not None else None

    # Un mouvement général éventuellement rattaché à une intervention.
    elif path == "/stock/mouvements/add":
        raw_id = request.form.get("intervention_id")
        if raw_id:
            try:
                intervention_id = int(raw_id)
            except (TypeError, ValueError):
                intervention_id = None

    if intervention_id is None:
        return None

    conn = get_db_connection()
    locked = _stock_intervention_is_locked(conn, intervention_id)
    conn.close()

    if locked:
        return redirect(f"/stock/intervention/{intervention_id}?locked=1")

    return None
# END STOCK_BEFORE_REPORT_WORKFLOW

'''


def install_backend(text: str) -> str:
    if BEGIN in text and END in text:
        return text

    if "# BEGIN STOCK_MODULE" not in text:
        raise RuntimeError("Le module stock n'est pas installé dans app.py.")

    markers = [
        "# ==========================\n# Lancement",
        'if __name__ == "__main__":',
    ]
    insert_at = -1
    for marker in markers:
        pos = text.find(marker)
        if pos != -1:
            insert_at = pos
            break
    if insert_at == -1:
        raise RuntimeError("Point d'insertion avant le lancement Flask introuvable.")

    return text[:insert_at] + BACKEND_BLOCK + text[insert_at:]


def install_base_workflow(text: str) -> str:
    # Si l'ancien garde global n'a pas encore été appliqué, on le pose d'abord.
    if "// BEGIN GLOBAL_REPORT_BUTTON_GUARD" not in text:
        try:
            from apply_global_report_button_guard import OLD_BLOCK, NEW_BLOCK
        except Exception as exc:
            raise RuntimeError(f"Impossible de charger le garde du bouton rapport : {exc}")
        if OLD_BLOCK not in text:
            raise RuntimeError("Bloc openIntervention attendu introuvable dans templates/base.html.")
        text = text.replace(OLD_BLOCK, NEW_BLOCK, 1)

    old_active = '''                    interventionActions = `
                        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;">
                            <a class="stock-btn-light" href="/stock/intervention/${data.id}">📦 Gérer les pièces</a>
                            <button class="btn-success"
                                onclick="openReportForm(${data.id}, '${data.scheduled_time || ''}')">
                                Rédiger le rapport
                            </button>
                        </div>`;'''

    new_active = '''                    interventionActions = `
                        <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:16px;">
                            <a class="btn-success" href="/stock/intervention/${data.id}" style="text-decoration:none;text-align:center;width:100%;">
                                📦 Gérer les pièces avant le rapport
                            </a>
                        </div>`;'''

    if old_active in text:
        text = text.replace(old_active, new_active, 1)
    elif "Gérer les pièces avant le rapport" not in text:
        raise RuntimeError("Branche intervention active introuvable dans templates/base.html.")

    text = text.replace(
        '<a class="stock-btn-light" href="/stock/intervention/${data.id}">📦 Gérer les pièces</a>\n                            <span class="badge status-completed">✓ Rapport déjà soumis</span>',
        '<a class="stock-btn-light" href="/stock/intervention/${data.id}">📦 Consulter les pièces</a>\n                            <span class="badge status-completed">✓ Rapport déjà soumis · pièces verrouillées</span>',
        1,
    )

    return text


def main() -> int:
    if not APP_PATH.exists() or not BASE_PATH.exists():
        print("ERREUR : app.py ou templates/base.html introuvable.")
        return 1

    app_text = APP_PATH.read_text(encoding="utf-8")
    base_text = BASE_PATH.read_text(encoding="utf-8")

    new_app = install_backend(app_text)
    new_base = install_base_workflow(base_text)

    if new_app == app_text and new_base == base_text:
        print("Le workflow pièces avant rapport est déjà installé.")
        return 0

    if new_app != app_text and not APP_BACKUP.exists():
        shutil.copy2(APP_PATH, APP_BACKUP)
        print(f"Sauvegarde créée : {APP_BACKUP.name}")
    if new_base != base_text and not BASE_BACKUP.exists():
        shutil.copy2(BASE_PATH, BASE_BACKUP)
        print(f"Sauvegarde créée : {BASE_BACKUP.name}")

    APP_PATH.write_text(new_app, encoding="utf-8")
    BASE_PATH.write_text(new_base, encoding="utf-8")

    print("Workflow pièces avant rapport installé.")
    print("- le rapport passe obligatoirement par la validation des pièces")
    print("- les réservations ouvertes doivent être consommées ou annulées")
    print("- après soumission du rapport, toutes les modifications de pièces sont bloquées")
    print("- l'historique des pièces reste consultable en lecture seule")
    print("Aucune migration PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERREUR : {exc}")
        sys.exit(1)
