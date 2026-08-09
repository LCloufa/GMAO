from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Dict, Iterable, List, Optional, Tuple


# Plages de référence utilisées par les indicateurs de disponibilité.
# Elles sont centralisées ici pour pouvoir les adapter facilement si nécessaire.
RYTHME_SCHEDULES = {
    "1x8": {
        "days": {0, 1, 2, 3, 4},
        "slots": [("08:00", "12:00"), ("13:00", "17:00")],
    },
    "2x8": {
        "days": {0, 1, 2, 3, 4},
        "slots": [("06:00", "22:00")],
    },
    "3x8": {
        "days": {0, 1, 2, 3, 4},
        "slots": [("00:00", "24:00")],
    },
    "24/7": {
        "days": {0, 1, 2, 3, 4, 5, 6},
        "slots": [("00:00", "24:00")],
    },
}


def normalize_rythme(value: Optional[str]) -> str:
    value = str(value or "1x8").strip()
    return value if value in RYTHME_SCHEDULES else "1x8"


def _parse_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, time.min)

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


def _parse_start(scheduled_date, scheduled_time) -> Optional[datetime]:
    if scheduled_date is None:
        return None

    if isinstance(scheduled_date, datetime):
        day = scheduled_date.date()
    elif isinstance(scheduled_date, date):
        day = scheduled_date
    else:
        try:
            day = date.fromisoformat(str(scheduled_date).strip()[:10])
        except ValueError:
            return None

    if isinstance(scheduled_time, time):
        clock = scheduled_time.replace(tzinfo=None) if scheduled_time.tzinfo else scheduled_time
    else:
        raw = str(scheduled_time or "08:00").strip()
        try:
            clock = time.fromisoformat(raw)
        except ValueError:
            clock = time(8, 0)

    return datetime.combine(day, clock)


def _slot_datetime(day: date, hhmm: str) -> datetime:
    if hhmm == "24:00":
        return datetime.combine(day + timedelta(days=1), time.min)
    hour, minute = (int(part) for part in hhmm.split(":"))
    return datetime.combine(day, time(hour, minute))


def working_intervals(start: datetime, end: datetime, rythme: str) -> List[Tuple[datetime, datetime]]:
    """Découpe [start, end] en intervalles couverts par le rythme du client."""
    if not start or not end or end <= start:
        return []

    schedule = RYTHME_SCHEDULES[normalize_rythme(rythme)]
    result: List[Tuple[datetime, datetime]] = []
    current_day = start.date()
    last_day = end.date()

    while current_day <= last_day:
        if current_day.weekday() in schedule["days"]:
            for slot_start, slot_end in schedule["slots"]:
                slot_a = _slot_datetime(current_day, slot_start)
                slot_b = _slot_datetime(current_day, slot_end)
                a = max(start, slot_a)
                b = min(end, slot_b)
                if a < b:
                    result.append((a, b))
        current_day += timedelta(days=1)

    return result


def merge_intervals(intervals: Iterable[Tuple[datetime, datetime]]) -> List[Tuple[datetime, datetime]]:
    ordered = sorted((a, b) for a, b in intervals if a < b)
    if not ordered:
        return []

    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def minutes_in_intervals(intervals: Iterable[Tuple[datetime, datetime]]) -> int:
    seconds = sum((end - start).total_seconds() for start, end in intervals)
    return max(0, int(seconds // 60))


def working_minutes_between(start: datetime, end: datetime, rythme: str) -> int:
    return minutes_in_intervals(working_intervals(start, end, rythme))


def _append_downtime_interval(
    intervals_by_equipment,
    equipment_info,
    equipment_id,
    start,
    end,
    period_start,
    period_end,
):
    """Ajoute un arrêt après limitation à la période et au rythme du client."""
    info = equipment_info.get(equipment_id)
    if not info:
        return

    start = _parse_datetime(start)
    end = _parse_datetime(end)
    if not start or not end or end <= start:
        return
    if start >= period_end or end <= period_start:
        return

    _, rythme = info
    clipped_start = max(start, period_start)
    clipped_end = min(end, period_end)
    if clipped_end <= clipped_start:
        return

    intervals_by_equipment[equipment_id].extend(
        working_intervals(clipped_start, clipped_end, rythme)
    )


def calculate_availability_metrics(conn, period_start: datetime, period_end: datetime, selected_client=None):
    """Calcule les indisponibilités réelles et les taux de disponibilité.

    Règle métier principale : lorsqu'une panne a été déclarée, l'indisponibilité
    commence à la date/heure de création de la déclaration, et non au démarrage
    planifié de l'intervention. Elle se termine à la première soumission du rapport
    lié à l'intervention. Tant qu'aucun rapport n'existe, la panne reste ouverte
    jusqu'à ``period_end`` (généralement maintenant).

    Pour les interventions qui ne proviennent d'aucune déclaration de panne
    (préventif, maintenance planifiée, intervention créée manuellement...), la
    logique historique est conservée : début planifié -> première soumission du
    rapport, ou -> ``period_end`` tant que le rapport n'existe pas.

    Les déclarations rejetées ne créent pas d'indisponibilité. Les temps hors
    rythme horaire du client ne sont pas comptés et les chevauchements d'arrêts
    d'un même équipement sont fusionnés.
    """
    cursor = conn.cursor()

    equipment_query = """
        SELECT e.id,
               e.client_id,
               COALESCE(c.nom, 'Sans client'),
               COALESCE(c.rythme_horaire, '1x8')
        FROM equipements e
        LEFT JOIN clients c ON e.client_id = c.id
    """
    equipment_params = []
    if selected_client:
        equipment_query += " WHERE e.client_id = ?"
        equipment_params.append(selected_client)

    cursor.execute(equipment_query, equipment_params)
    equipment_rows = cursor.fetchall()

    equipment_info = {}
    client_equipment_ids = defaultdict(list)
    client_names = {}
    client_rythmes = {}

    for row in equipment_rows:
        equipment_id, client_id, client_name, rythme = row
        rythme = normalize_rythme(rythme)
        equipment_info[equipment_id] = (client_id, rythme)
        client_equipment_ids[client_id].append(equipment_id)
        client_names[client_id] = client_name
        client_rythmes[client_id] = rythme

    intervals_by_equipment = defaultdict(list)

    # 1) Pannes déclarées : l'arrêt démarre dès la création de la déclaration.
    declaration_query = """
        SELECT d.id,
               d.equipment_id,
               d.created_at,
               d.updated_at,
               d.status,
               d.intervention_id,
               reports.report_created_at
        FROM declarations_panne d
        JOIN equipements e ON d.equipment_id = e.id
        LEFT JOIN (
            SELECT intervention_id, MIN(created_at) AS report_created_at
            FROM rapports_intervention
            GROUP BY intervention_id
        ) reports ON reports.intervention_id = d.intervention_id
        WHERE d.status <> 'rejected'
    """
    declaration_params = []
    if selected_client:
        declaration_query += " AND e.client_id = ?"
        declaration_params.append(selected_client)

    cursor.execute(declaration_query, declaration_params)
    declaration_rows = cursor.fetchall()

    for row in declaration_rows:
        (
            _,
            equipment_id,
            declaration_created_at,
            declaration_updated_at,
            declaration_status,
            _,
            report_created_at,
        ) = row

        start = _parse_datetime(declaration_created_at)
        if not start or start >= period_end:
            continue

        report_end = _parse_datetime(report_created_at)
        if report_end is not None:
            end = report_end
        elif str(declaration_status or "").lower() == "resolved":
            # Sécurité pour une déclaration clôturée manuellement sans rapport.
            end = _parse_datetime(declaration_updated_at) or period_end
        else:
            # Panne toujours ouverte : l'indisponibilité continue jusqu'à maintenant.
            end = period_end

        _append_downtime_interval(
            intervals_by_equipment,
            equipment_info,
            equipment_id,
            start,
            end,
            period_start,
            period_end,
        )

    # 2) Interventions sans déclaration : logique historique conservée.
    intervention_query = """
        SELECT i.id,
               i.equipment_id,
               i.scheduled_date,
               i.scheduled_time,
               i.status,
               reports.report_created_at
        FROM interventions i
        JOIN equipements e ON i.equipment_id = e.id
        LEFT JOIN (
            SELECT intervention_id, MIN(created_at) AS report_created_at
            FROM rapports_intervention
            GROUP BY intervention_id
        ) reports ON reports.intervention_id = i.id
        WHERE i.status NOT IN ('cancelled', 'postponed')
          AND NOT EXISTS (
              SELECT 1
              FROM declarations_panne d
              WHERE d.intervention_id = i.id
                AND d.status <> 'rejected'
          )
    """
    intervention_params = []
    if selected_client:
        intervention_query += " AND e.client_id = ?"
        intervention_params.append(selected_client)

    cursor.execute(intervention_query, intervention_params)
    intervention_rows = cursor.fetchall()

    for row in intervention_rows:
        _, equipment_id, scheduled_date, scheduled_time, _, report_created_at = row
        start = _parse_start(scheduled_date, scheduled_time)
        if not start or start >= period_end:
            continue

        report_end = _parse_datetime(report_created_at)
        end = report_end if report_end is not None else period_end

        _append_downtime_interval(
            intervals_by_equipment,
            equipment_info,
            equipment_id,
            start,
            end,
            period_start,
            period_end,
        )

    equipment_metrics: Dict[int, dict] = {}
    client_metrics: Dict[object, dict] = {}

    for equipment_id, (client_id, rythme) in equipment_info.items():
        downtime = minutes_in_intervals(merge_intervals(intervals_by_equipment[equipment_id]))
        capacity = working_minutes_between(period_start, period_end, rythme)
        rate = round(max(0.0, min(100.0, 100.0 - (downtime / max(1, capacity) * 100.0))), 1)
        equipment_metrics[equipment_id] = {
            "downtime_minutes": downtime,
            "capacity_minutes": capacity,
            "rate": rate,
            "client_id": client_id,
            "rythme_horaire": rythme,
        }

    global_downtime = 0
    global_capacity = 0

    for client_id, equipment_ids in client_equipment_ids.items():
        rythme = client_rythmes.get(client_id, "1x8")
        downtime = sum(equipment_metrics[eid]["downtime_minutes"] for eid in equipment_ids)
        capacity_per_equipment = working_minutes_between(period_start, period_end, rythme)
        capacity = capacity_per_equipment * len(equipment_ids)
        rate = round(max(0.0, min(100.0, 100.0 - (downtime / max(1, capacity) * 100.0))), 1)

        client_metrics[client_id] = {
            "client_id": client_id,
            "client_name": client_names.get(client_id, "Sans client"),
            "rythme_horaire": rythme,
            "equipment_count": len(equipment_ids),
            "downtime_minutes": downtime,
            "capacity_minutes": capacity,
            "rate": rate,
        }
        global_downtime += downtime
        global_capacity += capacity

    global_rate = round(
        max(0.0, min(100.0, 100.0 - (global_downtime / max(1, global_capacity) * 100.0))),
        1,
    )

    return {
        "clients": client_metrics,
        "equipements": equipment_metrics,
        "global_downtime_minutes": global_downtime,
        "global_capacity_minutes": global_capacity,
        "global_rate": global_rate,
    }
