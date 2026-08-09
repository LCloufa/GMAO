from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, jsonify, session

from database_compat import get_db_connection
from maintenance_metrics import calculate_availability_metrics


machine_dossier_bp = Blueprint("machine_dossier", __name__)


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
    return {
        "kind": kind,
        "title": title,
        "when": when,
        "detail": detail or "",
        "url": url,
        "severity": severity,
    }


@machine_dossier_bp.route("/api/equipements/<int:equipment_id>/dossier")
def equipment_dossier_data(equipment_id):
    if "user_id" not in session:
        return jsonify({"error": "Authentification requise"}), 401

    # Le moteur de disponibilité historique travaille avec des tuples.
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, client_id FROM equipements WHERE id = ?",
        (equipment_id,),
    )
    equipment_ref = cursor.fetchone()
    if not equipment_ref:
        conn.close()
        return jsonify({"error": "Équipement introuvable"}), 404

    client_id = equipment_ref[1]
    period_end = datetime.now()
    period_start = period_end - timedelta(days=30)
    availability = calculate_availability_metrics(
        conn,
        period_start=period_start,
        period_end=period_end,
        selected_client=client_id,
    )
    equipment_availability = availability.get("equipements", {}).get(equipment_id, {})

    # Le reste du dossier est plus pratique sous forme de dictionnaires.
    conn.row_factory = dict
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT e.*, c.nom AS client_nom, c.rythme_horaire
        FROM equipements e
        LEFT JOIN clients c ON c.id = e.client_id
        WHERE e.id = ?
        """,
        (equipment_id,),
    )
    equipment = cursor.fetchone()

    cursor.execute(
        """
        SELECT i.id, i.title, i.type, i.priority, i.status,
               i.scheduled_date, i.scheduled_time, i.estimated_duration,
               i.description, i.completion_date,
               t.code AS technicien_code,
               r.id AS rapport_id, r.etat AS rapport_etat,
               r.created_at AS rapport_created_at
        FROM interventions i
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        LEFT JOIN rapports_intervention r ON r.intervention_id = i.id
        WHERE i.equipment_id = ?
        ORDER BY i.scheduled_date DESC, i.scheduled_time DESC, i.id DESC
        LIMIT 100
        """,
        (equipment_id,),
    )
    interventions = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT d.id, d.title, d.description, d.urgency, d.location,
               d.status, d.created_at, d.updated_at, d.intervention_id,
               COALESCE(u.username, d.declared_by_name, '-') AS declarant
        FROM declarations_panne d
        LEFT JOIN users u ON u.id = d.declared_by_user_id
        WHERE d.equipment_id = ?
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT 100
        """,
        (equipment_id,),
    )
    declarations = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT isi.id, isi.intervention_id, isi.quantite_utilisee,
               isi.prix_unitaire, isi.created_at,
               a.id AS article_id, a.reference, a.designation, a.fabricant,
               i.title AS intervention_title
        FROM intervention_stock_items isi
        JOIN interventions i ON i.id = isi.intervention_id
        JOIN stock_articles a ON a.id = isi.article_id
        WHERE i.equipment_id = ?
        ORDER BY isi.created_at DESC, isi.id DESC
        LIMIT 100
        """,
        (equipment_id,),
    )
    parts = [_row_to_dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM rapports_intervention r
        JOIN interventions i ON i.id = r.intervention_id
        WHERE i.equipment_id = ?
        """,
        (equipment_id,),
    )
    reports_count = int((cursor.fetchone() or {}).get("total") or 0)

    conn.close()

    open_interventions = [
        item for item in interventions if item.get("status") in {"planned", "in_progress"}
    ]
    completed_interventions = [
        item for item in interventions if item.get("status") == "completed"
    ]
    open_failures = [
        item for item in declarations if item.get("status") in {"pending", "in_progress"}
    ]

    next_maintenance = None
    today = date.today().isoformat()
    planned = [
        item
        for item in interventions
        if item.get("status") == "planned"
        and str(item.get("scheduled_date") or "") >= today
    ]
    if planned:
        next_maintenance = sorted(
            planned,
            key=lambda item: (
                str(item.get("scheduled_date") or "9999-12-31"),
                str(item.get("scheduled_time") or "23:59"),
            ),
        )[0]

    parts_cost = 0.0
    for item in parts:
        qty = float(item.get("quantite_utilisee") or 0)
        price = float(item.get("prix_unitaire") or 0)
        parts_cost += qty * price

    timeline = []
    for declaration in declarations:
        timeline.append(
            _event(
                "failure",
                declaration.get("title") or "Panne déclarée",
                declaration.get("created_at"),
                declaration.get("description") or "",
                f"/declarations/{declaration['id']}",
                "danger" if declaration.get("urgency") == "critical" else "warning",
            )
        )

    for intervention in interventions:
        scheduled_date = intervention.get("scheduled_date")
        scheduled_time = intervention.get("scheduled_time") or "08:00"
        when = f"{scheduled_date}T{scheduled_time}" if scheduled_date else None
        timeline.append(
            _event(
                "intervention",
                intervention.get("title") or "Intervention",
                when,
                f"Statut : {intervention.get('status') or '-'}",
                f"/interventions?open={intervention['id']}",
            )
        )
        if intervention.get("rapport_id") and intervention.get("rapport_created_at"):
            timeline.append(
                _event(
                    "report",
                    f"Rapport : {intervention.get('title') or 'Intervention'}",
                    intervention.get("rapport_created_at"),
                    intervention.get("rapport_etat") or "Rapport enregistré",
                    f"/rapports/{intervention['rapport_id']}/pdf",
                    "success",
                )
            )

    for item in parts:
        qty = float(item.get("quantite_utilisee") or 0)
        timeline.append(
            _event(
                "part",
                f"{'Pièce consommée' if qty >= 0 else 'Pièce retournée'} : "
                f"{item.get('reference') or item.get('designation') or 'Article'}",
                item.get("created_at"),
                f"{abs(qty):g} × {item.get('designation') or ''}",
                f"/stock/articles/{item['article_id']}",
            )
        )

    timeline = [event for event in timeline if event]
    timeline.sort(key=lambda event: str(event.get("when") or ""), reverse=True)

    return jsonify(
        {
            "equipment": _row_to_dict(equipment),
            "kpis": {
                "availability_rate": float(equipment_availability.get("rate", 100.0)),
                "downtime_hours_30d": round(
                    float(equipment_availability.get("downtime_minutes", 0)) / 60.0,
                    1,
                ),
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
            "period": {
                "start": period_start.isoformat(timespec="seconds"),
                "end": period_end.isoformat(timespec="seconds"),
                "days": 30,
            },
        }
    )


def register_machine_dossier(app):
    if "machine_dossier" not in app.blueprints:
        app.register_blueprint(machine_dossier_bp)
