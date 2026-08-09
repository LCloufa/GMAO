from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import wraps

from flask import abort, redirect, render_template, request, session, url_for

from database_compat import get_db_connection
from maintenance_metrics import calculate_availability_metrics


MANAGER_SAFE_ENDPOINTS = {
    "manager_dashboard",
    "manager_parc",
    "manager_planning",
    "manager_analysis",
    "manager_reports",
    "manager_stock",
    "manager_alerts",
    "rapport_pdf",
    "export_rapports",
    "logout",
    "static",
}


def _as_int(value, default=0):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(text)
        return result.replace(tzinfo=None) if result.tzinfo else result
    except ValueError:
        return None


def _manager_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").lower() not in {"manager", "admin"}:
            return "Accès refusé", 403
        return view(*args, **kwargs)

    return wrapped


def _selected_client():
    raw = str(request.args.get("client") or "").strip()
    return raw or None


def _clients(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT id, nom FROM clients ORDER BY nom ASC")
    return cursor.fetchall()


def _equipment_health(rate, breakdowns, open_breakdowns, critical_open):
    if critical_open or rate < 85:
        return "critical"
    if rate < 93 or breakdowns >= 3 or open_breakdowns >= 2:
        return "degraded"
    if rate < 97 or breakdowns >= 1 or open_breakdowns >= 1:
        return "watch"
    return "healthy"


def _maintenance_snapshot(conn, selected_client=None):
    cursor = conn.cursor()
    period_end = datetime.now()
    period_start = period_end - timedelta(days=30)
    period_start_date = period_start.date().isoformat()
    today = date.today().isoformat()

    availability = calculate_availability_metrics(
        conn,
        period_start=period_start,
        period_end=period_end,
        selected_client=selected_client,
    )

    equipment_sql = """
        SELECT e.id,
               e.nom,
               COALESCE(c.nom, 'Sans client') AS client_nom,
               COALESCE(e.statut, '-') AS statut,
               COUNT(DISTINCT CASE
                   WHEN d.status <> 'rejected' AND d.created_at >= ? THEN d.id
               END) AS pannes_30j,
               COUNT(DISTINCT CASE
                   WHEN d.status IN ('pending', 'in_progress') THEN d.id
               END) AS pannes_ouvertes,
               MAX(CASE
                   WHEN d.status IN ('pending', 'in_progress') AND d.urgency = 'critical' THEN 1
                   ELSE 0
               END) AS panne_critique
        FROM equipements e
        LEFT JOIN clients c ON c.id = e.client_id
        LEFT JOIN declarations_panne d ON d.equipment_id = e.id
        WHERE 1=1
    """
    equipment_params = [period_start]
    if selected_client:
        equipment_sql += " AND e.client_id = ?"
        equipment_params.append(selected_client)
    equipment_sql += """
        GROUP BY e.id, e.nom, c.nom, e.statut
        ORDER BY COALESCE(c.nom, 'Sans client') ASC, e.nom ASC
    """
    cursor.execute(equipment_sql, equipment_params)
    equipment_rows = cursor.fetchall()

    cost_sql = """
        SELECT i.equipment_id,
               COALESCE(SUM(
                   CASE
                       WHEN m.type_mouvement = 'consommation' AND m.created_at >= ?
                       THEN ABS(m.quantite_delta) * COALESCE(m.prix_unitaire, 0)
                       ELSE 0
                   END
               ), 0) AS cout_30j
        FROM interventions i
        JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN stock_movements m ON m.intervention_id = i.id
        WHERE 1=1
    """
    cost_params = [period_start]
    if selected_client:
        cost_sql += " AND e.client_id = ?"
        cost_params.append(selected_client)
    cost_sql += " GROUP BY i.equipment_id"
    cursor.execute(cost_sql, cost_params)
    costs = {row[0]: _as_float(row[1]) for row in cursor.fetchall()}

    equipments = []
    for row in equipment_rows:
        equipment_id = row[0]
        metric = availability.get("equipements", {}).get(equipment_id, {})
        rate = _as_float(metric.get("rate"), 100.0)
        breakdowns = _as_int(row[4])
        open_breakdowns = _as_int(row[5])
        critical_open = bool(row[6])
        equipments.append(
            {
                "id": equipment_id,
                "name": row[1],
                "client": row[2],
                "status": row[3],
                "rate": rate,
                "downtime_minutes": _as_int(metric.get("downtime_minutes")),
                "breakdowns_30d": breakdowns,
                "open_breakdowns": open_breakdowns,
                "critical_open": critical_open,
                "cost_30d": round(costs.get(equipment_id, 0.0), 2),
                "health": _equipment_health(rate, breakdowns, open_breakdowns, critical_open),
            }
        )

    current_sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN i.status = 'planned' THEN 1 ELSE 0 END) AS planned,
               SUM(CASE WHEN i.status = 'in_progress' THEN 1 ELSE 0 END) AS in_progress
        FROM interventions i
        JOIN equipements e ON e.id = i.equipment_id
        WHERE 1=1
    """
    current_params = []
    if selected_client:
        current_sql += " AND e.client_id = ?"
        current_params.append(selected_client)
    cursor.execute(current_sql, current_params)
    current = cursor.fetchone() or (0, 0, 0)

    period_sql = """
        SELECT COUNT(*) AS total,
               SUM(CASE WHEN i.status = 'completed' THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN i.type = 'preventive' THEN 1 ELSE 0 END) AS preventive,
               SUM(CASE WHEN i.type = 'corrective' THEN 1 ELSE 0 END) AS corrective,
               SUM(CASE WHEN i.type = 'predictive' THEN 1 ELSE 0 END) AS predictive,
               SUM(CASE WHEN i.type = 'emergency' THEN 1 ELSE 0 END) AS emergency
        FROM interventions i
        JOIN equipements e ON e.id = i.equipment_id
        WHERE i.scheduled_date >= ?
    """
    period_params = [period_start_date]
    if selected_client:
        period_sql += " AND e.client_id = ?"
        period_params.append(selected_client)
    cursor.execute(period_sql, period_params)
    period = cursor.fetchone() or (0, 0, 0, 0, 0, 0)

    overdue_sql = """
        SELECT COUNT(*)
        FROM interventions i
        JOIN equipements e ON e.id = i.equipment_id
        WHERE i.status = 'planned' AND i.scheduled_date < ?
    """
    overdue_params = [today]
    if selected_client:
        overdue_sql += " AND e.client_id = ?"
        overdue_params.append(selected_client)
    cursor.execute(overdue_sql, overdue_params)
    overdue = _as_int(cursor.fetchone()[0])

    mttr_sql = """
        SELECT d.created_at, MIN(r.created_at)
        FROM declarations_panne d
        JOIN equipements e ON e.id = d.equipment_id
        JOIN rapports_intervention r ON r.intervention_id = d.intervention_id
        WHERE d.status <> 'rejected' AND d.created_at >= ?
    """
    mttr_params = [period_start]
    if selected_client:
        mttr_sql += " AND e.client_id = ?"
        mttr_params.append(selected_client)
    mttr_sql += " GROUP BY d.id, d.created_at"
    cursor.execute(mttr_sql, mttr_params)

    repair_minutes = []
    for start_value, end_value in cursor.fetchall():
        start_dt = _parse_datetime(start_value)
        end_dt = _parse_datetime(end_value)
        if start_dt and end_dt and end_dt >= start_dt:
            repair_minutes.append((end_dt - start_dt).total_seconds() / 60.0)

    mttr_hours = round((sum(repair_minutes) / len(repair_minutes)) / 60.0, 1) if repair_minutes else None
    breakdown_count = sum(item["breakdowns_30d"] for item in equipments)
    operating_minutes = max(
        0,
        _as_int(availability.get("global_capacity_minutes"))
        - _as_int(availability.get("global_downtime_minutes")),
    )
    mtbf_hours = round((operating_minutes / max(1, breakdown_count)) / 60.0, 1) if breakdown_count else None

    preventive = _as_int(period[2])
    corrective = _as_int(period[3]) + _as_int(period[5])
    preventive_corrective_total = preventive + corrective
    preventive_share = round((preventive / preventive_corrective_total) * 100.0, 1) if preventive_corrective_total else 0.0

    health_counts = {"healthy": 0, "watch": 0, "degraded": 0, "critical": 0}
    for equipment in equipments:
        health_counts[equipment["health"]] += 1

    return {
        "period_start": period_start,
        "period_end": period_end,
        "availability": availability,
        "global_rate": _as_float(availability.get("global_rate"), 100.0),
        "global_downtime_minutes": _as_int(availability.get("global_downtime_minutes")),
        "equipments": equipments,
        "equipment_count": len(equipments),
        "operational_count": health_counts["healthy"],
        "health_counts": health_counts,
        "planned": _as_int(current[1]),
        "in_progress": _as_int(current[2]),
        "completed_30d": _as_int(period[1]),
        "preventive": preventive,
        "corrective": corrective,
        "predictive": _as_int(period[4]),
        "emergency": _as_int(period[5]),
        "preventive_share": preventive_share,
        "breakdowns_30d": breakdown_count,
        "open_breakdowns": sum(item["open_breakdowns"] for item in equipments),
        "overdue": overdue,
        "mttr_hours": mttr_hours,
        "mtbf_hours": mtbf_hours,
    }


def _stock_snapshot(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.id,
               a.reference,
               a.designation,
               a.unite,
               a.stock_min,
               a.stock_max,
               a.prix_unitaire,
               COALESCE(m.physique, 0) AS physique,
               COALESCE(r.reserve, 0) AS reserve,
               COALESCE(l.nom, '-') AS emplacement,
               COALESCE(c.nom, '-') AS categorie
        FROM stock_articles a
        LEFT JOIN stock_locations l ON l.id = a.emplacement_id
        LEFT JOIN stock_categories c ON c.id = a.categorie_id
        LEFT JOIN (
            SELECT article_id, SUM(quantite_delta) AS physique
            FROM stock_movements
            GROUP BY article_id
        ) m ON m.article_id = a.id
        LEFT JOIN (
            SELECT article_id, SUM(quantite - quantite_consommee) AS reserve
            FROM stock_reservations
            WHERE statut = 'reserved'
            GROUP BY article_id
        ) r ON r.article_id = a.id
        WHERE a.actif = TRUE
        ORDER BY a.reference ASC
        """
    )

    items = []
    for row in cursor.fetchall():
        physique = _as_float(row[7])
        reserve = _as_float(row[8])
        disponible = physique - reserve
        stock_min = _as_float(row[4])
        prix = _as_float(row[6])
        if physique <= 0:
            state = "rupture"
        elif disponible <= stock_min:
            state = "low"
        else:
            state = "ok"
        items.append(
            {
                "id": row[0],
                "reference": row[1],
                "designation": row[2],
                "unite": row[3],
                "stock_min": stock_min,
                "stock_max": _as_float(row[5]) if row[5] is not None else None,
                "unit_price": prix,
                "physical": physique,
                "reserved": reserve,
                "available": disponible,
                "location": row[9],
                "category": row[10],
                "value": round(physique * prix, 2),
                "state": state,
            }
        )

    since = datetime.now() - timedelta(days=30)
    cursor.execute(
        """
        SELECT a.reference,
               a.designation,
               a.unite,
               COALESCE(SUM(ABS(m.quantite_delta)), 0) AS quantite,
               COALESCE(SUM(ABS(m.quantite_delta) * COALESCE(m.prix_unitaire, 0)), 0) AS cout
        FROM stock_movements m
        JOIN stock_articles a ON a.id = m.article_id
        WHERE m.type_mouvement = 'consommation' AND m.created_at >= ?
        GROUP BY a.id, a.reference, a.designation, a.unite
        ORDER BY cout DESC, quantite DESC
        LIMIT 10
        """,
        (since,),
    )
    top_consumption = [
        {
            "reference": row[0],
            "designation": row[1],
            "unite": row[2],
            "quantity": _as_float(row[3]),
            "cost": round(_as_float(row[4]), 2),
        }
        for row in cursor.fetchall()
    ]

    return {
        "items": items,
        "references": len(items),
        "value": round(sum(item["value"] for item in items), 2),
        "low": sum(1 for item in items if item["state"] == "low"),
        "ruptures": sum(1 for item in items if item["state"] == "rupture"),
        "alerts": [item for item in items if item["state"] != "ok"],
        "top_consumption": top_consumption,
    }


def _upcoming_interventions(conn, selected_client=None, days=14, limit=12):
    cursor = conn.cursor()
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    sql = """
        SELECT i.id,
               i.title,
               i.scheduled_date,
               i.scheduled_time,
               i.estimated_duration,
               i.priority,
               i.status,
               e.nom,
               COALESCE(t.code, '-'),
               COALESCE(c.nom, 'Sans client')
        FROM interventions i
        JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        LEFT JOIN clients c ON c.id = e.client_id
        WHERE i.status IN ('planned', 'in_progress')
          AND i.scheduled_date >= ?
          AND i.scheduled_date <= ?
    """
    params = [start, end]
    if selected_client:
        sql += " AND e.client_id = ?"
        params.append(selected_client)
    sql += " ORDER BY i.scheduled_date ASC, i.scheduled_time ASC LIMIT ?"
    params.append(limit)
    cursor.execute(sql, params)
    return [
        {
            "id": row[0],
            "title": row[1],
            "date": row[2],
            "time": row[3] or "-",
            "duration": _as_int(row[4]),
            "priority": row[5],
            "status": row[6],
            "equipment": row[7],
            "technician": row[8],
            "client": row[9],
        }
        for row in cursor.fetchall()
    ]


def _technician_load(conn, selected_client=None, days=7):
    cursor = conn.cursor()
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    sql = """
        SELECT COALESCE(t.code, 'Non assigné') AS technicien,
               COALESCE(SUM(i.estimated_duration), 0) AS minutes,
               COUNT(i.id) AS interventions
        FROM interventions i
        JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN techniciens t ON t.id = i.assigned_to
        WHERE i.status IN ('planned', 'in_progress')
          AND i.scheduled_date >= ?
          AND i.scheduled_date <= ?
    """
    params = [start, end]
    if selected_client:
        sql += " AND e.client_id = ?"
        params.append(selected_client)
    sql += " GROUP BY t.id, t.code ORDER BY minutes DESC"
    cursor.execute(sql, params)
    result = []
    for row in cursor.fetchall():
        minutes = _as_int(row[1])
        result.append(
            {
                "technician": row[0],
                "minutes": minutes,
                "hours": round(minutes / 60.0, 1),
                "interventions": _as_int(row[2]),
                "load": min(100, round((minutes / 2400.0) * 100)) if minutes else 0,
            }
        )
    return result


def _alerts(conn, maintenance, stock, selected_client=None):
    cursor = conn.cursor()
    alerts = []

    declaration_sql = """
        SELECT d.id, d.title, d.urgency, d.created_at, e.nom
        FROM declarations_panne d
        JOIN equipements e ON e.id = d.equipment_id
        WHERE d.status IN ('pending', 'in_progress')
          AND d.urgency IN ('critical', 'high')
    """
    declaration_params = []
    if selected_client:
        declaration_sql += " AND e.client_id = ?"
        declaration_params.append(selected_client)
    declaration_sql += " ORDER BY CASE WHEN d.urgency='critical' THEN 0 ELSE 1 END, d.created_at ASC LIMIT 10"
    cursor.execute(declaration_sql, declaration_params)
    for row in cursor.fetchall():
        alerts.append(
            {
                "level": "critical" if row[2] == "critical" else "warning",
                "title": f"Panne {row[2]} · {row[4]}",
                "detail": row[1],
                "kind": "maintenance",
            }
        )

    overdue_sql = """
        SELECT i.id, i.title, i.scheduled_date, e.nom
        FROM interventions i
        JOIN equipements e ON e.id = i.equipment_id
        WHERE i.status = 'planned' AND i.scheduled_date < ?
    """
    overdue_params = [date.today().isoformat()]
    if selected_client:
        overdue_sql += " AND e.client_id = ?"
        overdue_params.append(selected_client)
    overdue_sql += " ORDER BY i.scheduled_date ASC LIMIT 8"
    cursor.execute(overdue_sql, overdue_params)
    for row in cursor.fetchall():
        alerts.append(
            {
                "level": "warning",
                "title": f"Intervention en retard · {row[3]}",
                "detail": f"{row[1]} · prévue le {row[2]}",
                "kind": "planning",
            }
        )

    for equipment in maintenance["equipments"]:
        if equipment["health"] in {"critical", "degraded"}:
            alerts.append(
                {
                    "level": "critical" if equipment["health"] == "critical" else "warning",
                    "title": f"Équipement {equipment['health']} · {equipment['name']}",
                    "detail": f"Disponibilité {equipment['rate']:.1f}% · {equipment['breakdowns_30d']} panne(s) sur 30 j",
                    "kind": "parc",
                }
            )

    for item in stock["alerts"][:10]:
        if item["state"] == "rupture":
            alerts.append(
                {
                    "level": "critical",
                    "title": f"Rupture stock · {item['reference']}",
                    "detail": item["designation"],
                    "kind": "stock",
                }
            )
        else:
            alerts.append(
                {
                    "level": "warning",
                    "title": f"Sous seuil · {item['reference']}",
                    "detail": f"Disponible {item['available']:g} {item['unite']} · mini {item['stock_min']:g}",
                    "kind": "stock",
                }
            )

    priority = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda alert: priority.get(alert["level"], 9))
    return alerts


def _reports_snapshot(conn):
    q = str(request.args.get("q") or "").strip()
    etat = str(request.args.get("etat") or "").strip()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM rapports_intervention")
    total = _as_int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM rapports_intervention WHERE etat='Opérationnel'")
    ok = _as_int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM rapports_intervention WHERE etat='Nécessite un suivi'")
    follow_up = _as_int(cursor.fetchone()[0])
    cursor.execute("SELECT COUNT(*) FROM rapports_intervention WHERE etat='Toujours en panne'")
    down = _as_int(cursor.fetchone()[0])

    sql = """
        SELECT r.id,
               i.title,
               e.nom,
               COALESCE(c.nom, 'Sans client'),
               r.travaux,
               r.heure_debut,
               r.heure_fin,
               r.etat,
               r.observations,
               r.recommandations,
               r.created_at,
               COALESCE(u.username, '-')
        FROM rapports_intervention r
        LEFT JOIN interventions i ON i.id = r.intervention_id
        LEFT JOIN equipements e ON e.id = i.equipment_id
        LEFT JOIN clients c ON c.id = e.client_id
        LEFT JOIN users u ON u.id = r.created_by_user_id
        WHERE 1=1
    """
    params = []
    if etat:
        sql += " AND r.etat = ?"
        params.append(etat)
    if q:
        sql += " AND (LOWER(COALESCE(i.title,'')) LIKE ? OR LOWER(COALESCE(e.nom,'')) LIKE ? OR LOWER(COALESCE(r.travaux,'')) LIKE ?)"
        like = f"%{q.lower()}%"
        params.extend([like, like, like])
    sql += " ORDER BY r.created_at DESC LIMIT 250"
    cursor.execute(sql, params)
    reports = [
        {
            "id": row[0],
            "intervention": row[1] or "-",
            "equipment": row[2] or "-",
            "client": row[3],
            "work": row[4] or "-",
            "start": row[5] or "-",
            "end": row[6] or "-",
            "state": row[7] or "-",
            "observations": row[8] or "-",
            "recommendations": row[9] or "-",
            "created_at": row[10],
            "author": row[11],
        }
        for row in cursor.fetchall()
    ]
    return {
        "q": q,
        "etat": etat,
        "reports": reports,
        "total": total,
        "ok": ok,
        "follow_up": follow_up,
        "down": down,
    }


def register_manager_features(app):
    @app.route("/users/<int:user_id>/role-v2", methods=["POST"])
    def set_user_role_v2(user_id):
        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").lower() != "admin":
            return "Accès refusé", 403

        new_role = str(request.form.get("role") or "").strip().lower()
        if new_role not in {"operator", "technician", "manager"}:
            return "Rôle invalide.", 400
        if user_id == session.get("user_id"):
            return "Le rôle de votre propre compte administrateur est protégé.", 403

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, role FROM users WHERE id = ?", (user_id,))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return "Utilisateur introuvable", 404
        if str(user[1] or "").lower() == "admin":
            conn.close()
            return "Le rôle d'un administrateur est protégé.", 403

        cursor.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
        conn.close()
        return redirect("/users")

    @app.route("/manager")
    @_manager_required
    def manager_dashboard():
        selected_client = _selected_client()
        conn = get_db_connection()
        maintenance = _maintenance_snapshot(conn, selected_client)
        stock = _stock_snapshot(conn)
        alerts = _alerts(conn, maintenance, stock, selected_client)
        upcoming = _upcoming_interventions(conn, selected_client, days=14, limit=8)
        clients = _clients(conn)
        conn.close()

        top_risk = sorted(
            maintenance["equipments"],
            key=lambda item: (
                {"critical": 0, "degraded": 1, "watch": 2, "healthy": 3}[item["health"]],
                item["rate"],
                -item["breakdowns_30d"],
            ),
        )[:8]
        top_cost = sorted(maintenance["equipments"], key=lambda item: item["cost_30d"], reverse=True)[:6]

        return render_template(
            "manager_dashboard.html",
            maintenance=maintenance,
            stock=stock,
            alerts=alerts[:8],
            alert_count=len(alerts),
            upcoming=upcoming,
            top_risk=top_risk,
            top_cost=top_cost,
            clients=clients,
            selected_client=selected_client,
        )

    @app.route("/manager/parc")
    @_manager_required
    def manager_parc():
        selected_client = _selected_client()
        q = str(request.args.get("q") or "").strip().lower()
        health = str(request.args.get("health") or "").strip().lower()
        conn = get_db_connection()
        maintenance = _maintenance_snapshot(conn, selected_client)
        stock = _stock_snapshot(conn)
        alerts = _alerts(conn, maintenance, stock, selected_client)
        clients = _clients(conn)
        conn.close()

        equipments = maintenance["equipments"]
        if q:
            equipments = [item for item in equipments if q in item["name"].lower() or q in item["client"].lower()]
        if health in {"healthy", "watch", "degraded", "critical"}:
            equipments = [item for item in equipments if item["health"] == health]

        equipments = sorted(
            equipments,
            key=lambda item: (
                {"critical": 0, "degraded": 1, "watch": 2, "healthy": 3}[item["health"]],
                item["rate"],
            ),
        )
        return render_template(
            "manager_parc.html",
            maintenance=maintenance,
            equipments=equipments,
            q=q,
            health=health,
            clients=clients,
            selected_client=selected_client,
            alert_count=len(alerts),
        )

    @app.route("/manager/planning")
    @_manager_required
    def manager_planning():
        selected_client = _selected_client()
        conn = get_db_connection()
        maintenance = _maintenance_snapshot(conn, selected_client)
        stock = _stock_snapshot(conn)
        alerts = _alerts(conn, maintenance, stock, selected_client)
        planning = _upcoming_interventions(conn, selected_client, days=30, limit=250)
        technician_load = _technician_load(conn, selected_client, days=7)
        clients = _clients(conn)
        conn.close()
        return render_template(
            "manager_planning.html",
            planning=planning,
            technician_load=technician_load,
            clients=clients,
            selected_client=selected_client,
            alert_count=len(alerts),
        )

    @app.route("/manager/stock")
    @_manager_required
    def manager_stock():
        q = str(request.args.get("q") or "").strip().lower()
        conn = get_db_connection()
        stock = _stock_snapshot(conn)
        maintenance = _maintenance_snapshot(conn, None)
        alerts = _alerts(conn, maintenance, stock, None)
        conn.close()
        items = stock["items"]
        if q:
            items = [
                item
                for item in items
                if q in item["reference"].lower()
                or q in item["designation"].lower()
                or q in item["category"].lower()
                or q in item["location"].lower()
            ]
        return render_template(
            "manager_stock.html",
            stock=stock,
            items=items,
            q=q,
            alert_count=len(alerts),
        )

    @app.route("/manager/rapports")
    @_manager_required
    def manager_reports():
        conn = get_db_connection()
        reports = _reports_snapshot(conn)
        maintenance = _maintenance_snapshot(conn, None)
        stock = _stock_snapshot(conn)
        alerts = _alerts(conn, maintenance, stock, None)
        conn.close()
        return render_template(
            "manager_reports.html",
            reports_data=reports,
            alert_count=len(alerts),
        )

    @app.route("/manager/analyse")
    @_manager_required
    def manager_analysis():
        selected_client = _selected_client()
        conn = get_db_connection()
        maintenance = _maintenance_snapshot(conn, selected_client)
        stock = _stock_snapshot(conn)
        alerts = _alerts(conn, maintenance, stock, selected_client)
        technician_load = _technician_load(conn, selected_client, days=7)
        clients = _clients(conn)
        conn.close()

        top_downtime = sorted(maintenance["equipments"], key=lambda item: item["downtime_minutes"], reverse=True)[:10]
        top_breakdowns = sorted(maintenance["equipments"], key=lambda item: item["breakdowns_30d"], reverse=True)[:10]
        top_cost = sorted(maintenance["equipments"], key=lambda item: item["cost_30d"], reverse=True)[:10]

        return render_template(
            "manager_analysis.html",
            maintenance=maintenance,
            stock=stock,
            alerts=alerts,
            alert_count=len(alerts),
            technician_load=technician_load,
            top_downtime=top_downtime,
            top_breakdowns=top_breakdowns,
            top_cost=top_cost,
            clients=clients,
            selected_client=selected_client,
        )

    @app.route("/manager/alertes")
    @_manager_required
    def manager_alerts():
        selected_client = _selected_client()
        conn = get_db_connection()
        maintenance = _maintenance_snapshot(conn, selected_client)
        stock = _stock_snapshot(conn)
        alerts = _alerts(conn, maintenance, stock, selected_client)
        clients = _clients(conn)
        conn.close()
        return render_template(
            "manager_alerts.html",
            alerts=alerts,
            alert_count=len(alerts),
            clients=clients,
            selected_client=selected_client,
        )

    @app.before_request
    def restrict_manager_access():
        role = str(session.get("role") or "").strip().lower()
        if role != "manager":
            return None

        endpoint = request.endpoint or ""
        if endpoint == "dashboard":
            return redirect(url_for("manager_dashboard"))

        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            return "Accès refusé : le profil Manager est en lecture seule.", 403

        if endpoint in MANAGER_SAFE_ENDPOINTS:
            return None

        return "Accès refusé : le profil Manager est limité aux vues de pilotage en lecture seule.", 403
