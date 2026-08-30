from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, session

from database_compat import get_db_connection
from maintenance_metrics import calculate_availability_metrics


machine_dossier_bp = Blueprint("machine_dossier", __name__)

DOSSIER_TABLES = (
    "equipement_components",
    "equipement_specifications",
    "equipement_counters",
    "equipement_counter_readings",
    "equipement_parts",
)


def _serialise(value):
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _row_to_dict(row):
    return {key: _serialise(value) for key, value in dict(row).items()}


def _event(kind, title, when, detail="", url=None, severity="info"):
    when = _serialise(when)
    if not when:
        return None
    return {"kind": kind, "title": title, "when": when, "detail": detail or "", "url": url, "severity": severity}


def _schema_ready(conn):
    cursor = conn.cursor()
    for table in DOSSIER_TABLES:
        cursor.execute("SELECT to_regclass(?)", (f"public.{table}",))
        row = cursor.fetchone()
        if not row or not row[0]:
            return False
    return True


def _require_auth():
    if "user_id" not in session:
        return jsonify({"error": "Authentification requise"}), 401
    return None


def _require_editor():
    auth = _require_auth()
    if auth:
        return auth
    if str(session.get("role") or "").lower() not in {"admin", "technician"}:
        return jsonify({"error": "Accès en écriture réservé aux administrateurs et techniciens"}), 403
    return None


def _migration_required():
    return jsonify({"error": "La migration Dossier Machine Numérique n'est pas encore appliquée.", "migration": "c7d4e2a91f30"}), 503


def _equipment_exists(conn, equipment_id):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM equipements WHERE id = ?", (equipment_id,))
    return bool(cursor.fetchone())


def _component_belongs(conn, equipment_id, component_id):
    if component_id in (None, "", 0, "0"):
        return True
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM equipement_components WHERE id = ? AND equipement_id = ?", (component_id, equipment_id))
    return bool(cursor.fetchone())


def _to_decimal(value, required=False):
    if value in (None, ""):
        if required:
            raise ValueError("Valeur numérique obligatoire")
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Valeur numérique invalide")


def _load_extended_dossier(conn, equipment_id):
    if not _schema_ready(conn):
        return {"schema_ready": False, "components": [], "specifications": [], "counters": [], "compatible_parts": []}

    conn.row_factory = dict
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, equipement_id, parent_id, code, nom, type_composant, criticite,
               fabricant, modele, numero_serie, notes, ordre, actif, created_at, updated_at
        FROM equipement_components
        WHERE equipement_id = ?
        ORDER BY COALESCE(parent_id, 0), ordre, nom, id
    """, (equipment_id,))
    components = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT s.id, s.equipement_id, s.component_id, s.groupe, s.nom, s.valeur,
               s.unite, s.type_valeur, s.ordre, c.nom AS component_nom
        FROM equipement_specifications s
        LEFT JOIN equipement_components c ON c.id = s.component_id
        WHERE s.equipement_id = ?
        ORDER BY COALESCE(s.groupe, ''), s.ordre, s.nom, s.id
    """, (equipment_id,))
    specifications = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT c.id, c.equipement_id, c.component_id, c.nom, c.unite, c.type_compteur,
               c.actif, c.created_at, comp.nom AS component_nom,
               r.valeur AS valeur_actuelle, r.releve_at AS dernier_releve_at,
               r.note AS dernier_releve_note
        FROM equipement_counters c
        LEFT JOIN equipement_components comp ON comp.id = c.component_id
        LEFT JOIN LATERAL (
            SELECT valeur, releve_at, note
            FROM equipement_counter_readings
            WHERE counter_id = c.id
            ORDER BY releve_at DESC, id DESC
            LIMIT 1
        ) r ON TRUE
        WHERE c.equipement_id = ?
        ORDER BY c.actif DESC, c.nom, c.id
    """, (equipment_id,))
    counters = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT ep.id, ep.equipement_id, ep.component_id, ep.article_id,
               ep.quantite_recommandee, ep.critique, ep.notes, ep.created_at,
               a.reference, a.designation, a.fabricant, a.prix_unitaire,
               comp.nom AS component_nom
        FROM equipement_parts ep
        JOIN stock_articles a ON a.id = ep.article_id
        LEFT JOIN equipement_components comp ON comp.id = ep.component_id
        WHERE ep.equipement_id = ?
        ORDER BY ep.critique DESC, a.reference, ep.id
    """, (equipment_id,))
    compatible_parts = [_row_to_dict(row) for row in cursor.fetchall()]

    return {"schema_ready": True, "components": components, "specifications": specifications, "counters": counters, "compatible_parts": compatible_parts}


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/dossier")
def equipment_dossier_data(equipment_id):
    auth = _require_auth()
    if auth:
        return auth

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, client_id FROM equipements WHERE id = ?", (equipment_id,))
    equipment_ref = cursor.fetchone()
    if not equipment_ref:
        conn.close()
        return jsonify({"error": "Équipement introuvable"}), 404

    client_id = equipment_ref[1]
    period_end = datetime.now()
    period_start = period_end - timedelta(days=30)
    availability = calculate_availability_metrics(conn, period_start=period_start, period_end=period_end, selected_client=client_id)
    equipment_availability = availability.get("equipements", {}).get(equipment_id, {})

    conn.row_factory = dict
    cursor = conn.cursor()
    cursor.execute("""
        SELECT e.*, c.nom AS client_nom, c.rythme_horaire
        FROM equipements e
        LEFT JOIN clients c ON c.id = e.client_id
        WHERE e.id = ?
    """, (equipment_id,))
    equipment = cursor.fetchone()

    cursor.execute("""
        SELECT i.id, i.title, i.type, i.priority, i.status, i.scheduled_date,
               i.scheduled_time, i.estimated_duration, i.description, i.completion_date,
               t.code AS technicien_code, r.id AS rapport_id, r.etat AS rapport_etat,
               r.created_at AS rapport_created_at
        FROM interventions i
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        LEFT JOIN rapports_intervention r ON r.intervention_id = i.id
        WHERE i.equipment_id = ?
        ORDER BY i.scheduled_date DESC, i.scheduled_time DESC, i.id DESC
        LIMIT 100
    """, (equipment_id,))
    interventions = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT d.id, d.title, d.description, d.urgency, d.location, d.status,
               d.created_at, d.updated_at, d.intervention_id,
               COALESCE(u.username, d.declared_by_name, '-') AS declarant
        FROM declarations_panne d
        LEFT JOIN users u ON u.id = d.declared_by_user_id
        WHERE d.equipment_id = ?
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT 100
    """, (equipment_id,))
    declarations = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT isi.id, isi.intervention_id, isi.quantite_utilisee, isi.prix_unitaire,
               isi.created_at, a.id AS article_id, a.reference, a.designation,
               a.fabricant, i.title AS intervention_title
        FROM intervention_stock_items isi
        JOIN interventions i ON i.id = isi.intervention_id
        JOIN stock_articles a ON a.id = isi.article_id
        WHERE i.equipment_id = ?
        ORDER BY isi.created_at DESC, isi.id DESC
        LIMIT 100
    """, (equipment_id,))
    parts = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute("""
        SELECT COUNT(*) AS total
        FROM rapports_intervention r
        JOIN interventions i ON i.id = r.intervention_id
        WHERE i.equipment_id = ?
    """, (equipment_id,))
    reports_count = int((cursor.fetchone() or {}).get("total") or 0)

    extended = _load_extended_dossier(conn, equipment_id)
    conn.close()

    open_interventions = [item for item in interventions if item.get("status") in {"planned", "in_progress"}]
    completed_interventions = [item for item in interventions if item.get("status") == "completed"]
    open_failures = [item for item in declarations if item.get("status") in {"pending", "in_progress"}]

    next_maintenance = None
    today = date.today().isoformat()
    planned = [item for item in interventions if item.get("status") == "planned" and str(item.get("scheduled_date") or "") >= today]
    if planned:
        next_maintenance = sorted(planned, key=lambda item: (str(item.get("scheduled_date") or "9999-12-31"), str(item.get("scheduled_time") or "23:59")))[0]

    parts_cost = sum(float(item.get("quantite_utilisee") or 0) * float(item.get("prix_unitaire") or 0) for item in parts)

    timeline = []
    for declaration in declarations:
        timeline.append(_event("failure", declaration.get("title") or "Panne déclarée", declaration.get("created_at"), declaration.get("description") or "", f"/declarations/{declaration['id']}", "danger" if declaration.get("urgency") == "critical" else "warning"))
    for intervention in interventions:
        scheduled_date = intervention.get("scheduled_date")
        scheduled_time = intervention.get("scheduled_time") or "08:00"
        when = f"{scheduled_date}T{scheduled_time}" if scheduled_date else None
        timeline.append(_event("intervention", intervention.get("title") or "Intervention", when, f"Statut : {intervention.get('status') or '-'}", f"/interventions?open={intervention['id']}"))
        if intervention.get("rapport_id") and intervention.get("rapport_created_at"):
            timeline.append(_event("report", f"Rapport : {intervention.get('title') or 'Intervention'}", intervention.get("rapport_created_at"), intervention.get("rapport_etat") or "Rapport enregistré", f"/rapports/{intervention['rapport_id']}/pdf", "success"))
    for item in parts:
        qty = float(item.get("quantite_utilisee") or 0)
        timeline.append(_event("part", f"{'Pièce consommée' if qty >= 0 else 'Pièce retournée'} : {item.get('reference') or item.get('designation') or 'Article'}", item.get("created_at"), f"{abs(qty):g} × {item.get('designation') or ''}", f"/stock/articles/{item['article_id']}"))

    timeline = [event for event in timeline if event]
    timeline.sort(key=lambda event: str(event.get("when") or ""), reverse=True)

    return jsonify({
        "equipment": _row_to_dict(equipment),
        "schema_ready": extended["schema_ready"],
        "migration_required": None if extended["schema_ready"] else "c7d4e2a91f30",
        "kpis": {
            "availability_rate": float(equipment_availability.get("rate", 100.0)),
            "downtime_hours_30d": round(float(equipment_availability.get("downtime_minutes", 0)) / 60.0, 1),
            "total_interventions": len(interventions),
            "open_interventions": len(open_interventions),
            "completed_interventions": len(completed_interventions),
            "open_failures": len(open_failures),
            "reports_count": reports_count,
            "parts_cost": round(parts_cost, 2),
        },
        "last_failure": declarations[0] if declarations else None,
        "next_maintenance": next_maintenance,
        "interventions": interventions[:40],
        "declarations": declarations[:40],
        "parts": parts[:40],
        "timeline": timeline[:60],
        "components": extended["components"],
        "specifications": extended["specifications"],
        "counters": extended["counters"],
        "compatible_parts": extended["compatible_parts"],
        "period": {"start": period_start.isoformat(timespec="seconds"), "end": period_end.isoformat(timespec="seconds"), "days": 30},
    })


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/components", methods=["POST"])
def create_component(equipment_id):
    denied = _require_editor()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    nom = str(payload.get("nom") or "").strip()
    if not nom:
        return jsonify({"error": "Le nom du composant est obligatoire"}), 400
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    if not _equipment_exists(conn, equipment_id):
        conn.close(); return jsonify({"error": "Équipement introuvable"}), 404
    parent_id = payload.get("parent_id") or None
    if not _component_belongs(conn, equipment_id, parent_id):
        conn.close(); return jsonify({"error": "Le parent n'appartient pas à cette machine"}), 400
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO equipement_components
        (equipement_id, parent_id, code, nom, type_composant, criticite, fabricant, modele, numero_serie, notes, ordre)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
    """, (equipment_id, parent_id, str(payload.get("code") or "").strip() or None, nom, str(payload.get("type_composant") or "").strip() or None, str(payload.get("criticite") or "medium").strip() or "medium", str(payload.get("fabricant") or "").strip() or None, str(payload.get("modele") or "").strip() or None, str(payload.get("numero_serie") or "").strip() or None, str(payload.get("notes") or "").strip() or None, int(payload.get("ordre") or 0)))
    row_id = cursor.fetchone()[0]
    conn.commit(); conn.close()
    return jsonify({"ok": True, "id": row_id}), 201


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/components/<int:component_id>", methods=["DELETE"])
def delete_component(equipment_id, component_id):
    denied = _require_editor()
    if denied:
        return denied
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM equipement_components WHERE parent_id = ? AND equipement_id = ?", (component_id, equipment_id))
    if int(cursor.fetchone()[0] or 0):
        conn.close(); return jsonify({"error": "Supprimez d'abord les sous-composants de cet élément"}), 409
    cursor.execute("DELETE FROM equipement_components WHERE id = ? AND equipement_id = ?", (component_id, equipment_id))
    conn.commit(); conn.close()
    return jsonify({"ok": True})


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/specifications", methods=["POST"])
def create_specification(equipment_id):
    denied = _require_editor()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    nom = str(payload.get("nom") or "").strip()
    if not nom:
        return jsonify({"error": "Le nom de la caractéristique est obligatoire"}), 400
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    component_id = payload.get("component_id") or None
    if not _component_belongs(conn, equipment_id, component_id):
        conn.close(); return jsonify({"error": "Composant invalide pour cette machine"}), 400
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO equipement_specifications
        (equipement_id, component_id, groupe, nom, valeur, unite, type_valeur, ordre)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
    """, (equipment_id, component_id, str(payload.get("groupe") or "").strip() or None, nom, str(payload.get("valeur") or "").strip() or None, str(payload.get("unite") or "").strip() or None, str(payload.get("type_valeur") or "text").strip() or "text", int(payload.get("ordre") or 0)))
    row_id = cursor.fetchone()[0]
    conn.commit(); conn.close()
    return jsonify({"ok": True, "id": row_id}), 201


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/specifications/<int:specification_id>", methods=["DELETE"])
def delete_specification(equipment_id, specification_id):
    denied = _require_editor()
    if denied:
        return denied
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    cursor = conn.cursor(); cursor.execute("DELETE FROM equipement_specifications WHERE id = ? AND equipement_id = ?", (specification_id, equipment_id))
    conn.commit(); conn.close(); return jsonify({"ok": True})


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/counters", methods=["POST"])
def create_counter(equipment_id):
    denied = _require_editor()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    nom = str(payload.get("nom") or "").strip()
    if not nom:
        return jsonify({"error": "Le nom du compteur est obligatoire"}), 400
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    component_id = payload.get("component_id") or None
    if not _component_belongs(conn, equipment_id, component_id):
        conn.close(); return jsonify({"error": "Composant invalide pour cette machine"}), 400
    cursor = conn.cursor()
    cursor.execute("INSERT INTO equipement_counters (equipement_id, component_id, nom, unite, type_compteur) VALUES (?, ?, ?, ?, ?) RETURNING id", (equipment_id, component_id, nom, str(payload.get("unite") or "h").strip() or "h", str(payload.get("type_compteur") or "usage").strip() or "usage"))
    counter_id = cursor.fetchone()[0]
    initial_value = payload.get("valeur_initiale")
    if initial_value not in (None, ""):
        try:
            numeric_value = _to_decimal(initial_value, required=True)
        except ValueError as exc:
            conn.rollback(); conn.close(); return jsonify({"error": str(exc)}), 400
        cursor.execute("INSERT INTO equipement_counter_readings (counter_id, valeur, created_by_user_id, note) VALUES (?, ?, ?, ?)", (counter_id, numeric_value, session.get("user_id"), "Valeur initiale"))
    conn.commit(); conn.close(); return jsonify({"ok": True, "id": counter_id}), 201


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/counters/<int:counter_id>/readings", methods=["POST"])
def create_counter_reading(equipment_id, counter_id):
    denied = _require_editor()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    try:
        value = _to_decimal(payload.get("valeur"), required=True)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    cursor = conn.cursor(); cursor.execute("SELECT 1 FROM equipement_counters WHERE id = ? AND equipement_id = ?", (counter_id, equipment_id))
    if not cursor.fetchone():
        conn.close(); return jsonify({"error": "Compteur introuvable"}), 404
    cursor.execute("INSERT INTO equipement_counter_readings (counter_id, valeur, created_by_user_id, note) VALUES (?, ?, ?, ?) RETURNING id", (counter_id, value, session.get("user_id"), str(payload.get("note") or "").strip() or None))
    row_id = cursor.fetchone()[0]
    conn.commit(); conn.close(); return jsonify({"ok": True, "id": row_id}), 201


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/counters/<int:counter_id>", methods=["DELETE"])
def delete_counter(equipment_id, counter_id):
    denied = _require_editor()
    if denied:
        return denied
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    cursor = conn.cursor(); cursor.execute("DELETE FROM equipement_counters WHERE id = ? AND equipement_id = ?", (counter_id, equipment_id))
    conn.commit(); conn.close(); return jsonify({"ok": True})


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/compatible-parts", methods=["POST"])
def create_compatible_part(equipment_id):
    denied = _require_editor()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    reference = str(payload.get("reference") or "").strip()
    if not reference:
        return jsonify({"error": "La référence stock est obligatoire"}), 400
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    component_id = payload.get("component_id") or None
    if not _component_belongs(conn, equipment_id, component_id):
        conn.close(); return jsonify({"error": "Composant invalide pour cette machine"}), 400
    cursor = conn.cursor(); cursor.execute("SELECT id FROM stock_articles WHERE reference = ? AND actif = TRUE", (reference,))
    article = cursor.fetchone()
    if not article:
        conn.close(); return jsonify({"error": f"Article stock introuvable : {reference}"}), 404
    try:
        qty = _to_decimal(payload.get("quantite_recommandee"))
    except ValueError as exc:
        conn.close(); return jsonify({"error": str(exc)}), 400
    cursor.execute("""
        SELECT id FROM equipement_parts
        WHERE equipement_id = ? AND article_id = ?
          AND ((component_id IS NULL AND ? IS NULL) OR component_id = ?)
        LIMIT 1
    """, (equipment_id, article[0], component_id, component_id))
    if cursor.fetchone():
        conn.close(); return jsonify({"error": "Cette pièce est déjà liée à ce niveau de la machine"}), 409
    cursor.execute("INSERT INTO equipement_parts (equipement_id, component_id, article_id, quantite_recommandee, critique, notes) VALUES (?, ?, ?, ?, ?, ?) RETURNING id", (equipment_id, component_id, article[0], qty, bool(payload.get("critique")), str(payload.get("notes") or "").strip() or None))
    row_id = cursor.fetchone()[0]
    conn.commit(); conn.close(); return jsonify({"ok": True, "id": row_id}), 201


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/compatible-parts/<int:link_id>", methods=["DELETE"])
def delete_compatible_part(equipment_id, link_id):
    denied = _require_editor()
    if denied:
        return denied
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close(); return _migration_required()
    cursor = conn.cursor(); cursor.execute("DELETE FROM equipement_parts WHERE id = ? AND equipement_id = ?", (link_id, equipment_id))
    conn.commit(); conn.close(); return jsonify({"ok": True})


def register_machine_dossier(app):
    if "machine_dossier" not in app.blueprints:
        app.register_blueprint(machine_dossier_bp)
