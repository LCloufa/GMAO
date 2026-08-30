from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, session

from database_compat import get_db_connection


machine_part_links_bp = Blueprint("machine_part_links", __name__)


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


def _to_int(value, label):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} invalide")
    if parsed <= 0:
        raise ValueError(f"{label} invalide")
    return parsed


def _to_decimal(value):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Quantité recommandée invalide")
    if parsed < 0:
        raise ValueError("La quantité recommandée doit être positive")
    return parsed


def _first_value(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return next(iter(row.values()), None)
    try:
        return row[0]
    except (KeyError, IndexError, TypeError):
        return None


def _equipment_exists(conn, equipment_id):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM equipements WHERE id = ?", (equipment_id,))
    return bool(cursor.fetchone())


def _schema_ready(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT to_regclass(?)", ("public.equipement_parts",))
    return bool(_first_value(cursor.fetchone()))


def _component_target(conn, equipment_id, component_id):
    conn.row_factory = dict
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT c.id, c.nom, c.code,
               p.id AS sous_ensemble_id, p.nom AS sous_ensemble_nom,
               g.id AS ensemble_id, g.nom AS ensemble_nom
        FROM equipement_components c
        JOIN equipement_components p ON p.id = c.parent_id
        JOIN equipement_components g ON g.id = p.parent_id
        WHERE c.id = ?
          AND c.equipement_id = ?
          AND p.equipement_id = ?
          AND g.equipement_id = ?
          AND g.parent_id IS NULL
        LIMIT 1
        """,
        (component_id, equipment_id, equipment_id, equipment_id),
    )
    return cursor.fetchone()


@machine_part_links_bp.route("/api/equipements/<int:equipment_id>/part-link-options", methods=["GET"])
def part_link_options(equipment_id):
    denied = _require_auth()
    if denied:
        return denied

    conn = get_db_connection()
    if not _equipment_exists(conn, equipment_id):
        conn.close()
        return jsonify({"error": "Équipement introuvable"}), 404
    if not _schema_ready(conn):
        conn.close()
        return jsonify({"error": "Le module de nomenclature machine n'est pas encore disponible."}), 503

    conn.row_factory = dict
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, reference, designation, fabricant, prix_unitaire
        FROM stock_articles
        WHERE actif = TRUE
        ORDER BY reference, designation, id
        LIMIT 2000
        """
    )
    articles = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT c.id, c.nom, c.code,
               p.nom AS sous_ensemble_nom,
               g.nom AS ensemble_nom
        FROM equipement_components c
        JOIN equipement_components p ON p.id = c.parent_id
        JOIN equipement_components g ON g.id = p.parent_id
        WHERE c.equipement_id = ?
          AND p.equipement_id = ?
          AND g.equipement_id = ?
          AND g.parent_id IS NULL
        ORDER BY g.nom, p.nom, c.nom, c.id
        """,
        (equipment_id, equipment_id, equipment_id),
    )
    components = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return jsonify({"articles": articles, "components": components})


@machine_part_links_bp.route("/api/equipements/<int:equipment_id>/part-links", methods=["POST"])
def create_part_link(equipment_id):
    denied = _require_editor()
    if denied:
        return denied

    payload = request.get_json(silent=True) or {}
    try:
        article_id = _to_int(payload.get("article_id"), "Article stock")
        component_id = _to_int(payload.get("component_id"), "Composant")
        qty = _to_decimal(payload.get("quantite_recommandee"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    conn = get_db_connection()
    if not _equipment_exists(conn, equipment_id):
        conn.close()
        return jsonify({"error": "Équipement introuvable"}), 404
    if not _schema_ready(conn):
        conn.close()
        return jsonify({"error": "Le module de nomenclature machine n'est pas encore disponible."}), 503

    cursor = conn.cursor()
    cursor.execute("SELECT id FROM stock_articles WHERE id = ? AND actif = TRUE", (article_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({"error": "Article stock introuvable ou inactif"}), 404

    target = _component_target(conn, equipment_id, component_id)
    if not target:
        conn.close()
        return jsonify({"error": "Le composant choisi n'est pas un composant du troisième niveau de cette machine."}), 400

    conn.row_factory = None
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id
        FROM equipement_parts
        WHERE equipement_id = ? AND component_id = ? AND article_id = ?
        LIMIT 1
        """,
        (equipment_id, component_id, article_id),
    )
    if cursor.fetchone():
        conn.close()
        return jsonify({"error": "Cette pièce de stock est déjà liée à ce composant."}), 409

    cursor.execute(
        """
        INSERT INTO equipement_parts
        (equipement_id, component_id, article_id, quantite_recommandee, critique, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            equipment_id,
            component_id,
            article_id,
            qty,
            bool(payload.get("critique")),
            str(payload.get("notes") or "").strip() or None,
        ),
    )
    link_id = _first_value(cursor.fetchone())
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "id": link_id}), 201


def register_machine_part_links(app):
    if machine_part_links_bp.name not in app.blueprints:
        app.register_blueprint(machine_part_links_bp)
