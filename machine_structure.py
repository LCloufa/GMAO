from __future__ import annotations

from decimal import Decimal, InvalidOperation

from flask import Blueprint, jsonify, request, session

from database_compat import get_db_connection


machine_structure_bp = Blueprint("machine_structure", __name__)
MIGRATION_REVISION = "e14f6a7c2b90"


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


def _schema_ready(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'equipement_components'
          AND column_name IN ('supplier_id', 'delai_obtention_jours', 'prix_unitaire')
        """
    )
    row = cursor.fetchone()
    return bool(row and int(row[0] or 0) == 3)


def _migration_required():
    return jsonify({
        "error": "La migration de la nouvelle arborescence technique n'est pas encore appliquée.",
        "migration": MIGRATION_REVISION,
    }), 503


def _equipment_exists(conn, equipment_id):
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM equipements WHERE id = ?", (equipment_id,))
    return bool(cursor.fetchone())


def _component_row(conn, equipment_id, component_id):
    conn.row_factory = dict
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, equipement_id, parent_id, nom, type_composant
        FROM equipement_components
        WHERE id = ? AND equipement_id = ?
        """,
        (component_id, equipment_id),
    )
    return cursor.fetchone()


def _component_depth(conn, equipment_id, component_id):
    if not component_id:
        return -1
    depth = 0
    current_id = int(component_id)
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        row = _component_row(conn, equipment_id, current_id)
        if not row:
            return None
        parent_id = row.get("parent_id")
        if not parent_id:
            return depth
        depth += 1
        current_id = int(parent_id)
        if depth > 20:
            return None
    return depth


def _supplier_id(conn, value):
    if value in (None, "", 0, "0"):
        return None
    try:
        supplier_id = int(value)
    except (TypeError, ValueError):
        raise ValueError("Fournisseur invalide")
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM stock_suppliers WHERE id = ?", (supplier_id,))
    if not cursor.fetchone():
        raise ValueError("Fournisseur introuvable")
    return supplier_id


def _non_negative_int(value, label):
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} invalide")
    if parsed < 0:
        raise ValueError(f"{label} doit être positif")
    return parsed


def _non_negative_decimal(value, label):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} invalide")
    if parsed < 0:
        raise ValueError(f"{label} doit être positif")
    return parsed


def _load_structure(conn, equipment_id):
    conn.row_factory = dict
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT ec.id, ec.equipement_id, ec.parent_id, ec.code, ec.nom,
               ec.type_composant, ec.criticite, ec.fabricant, ec.modele,
               ec.numero_serie, ec.notes, ec.ordre, ec.actif,
               ec.supplier_id, ec.delai_obtention_jours, ec.prix_unitaire,
               s.nom AS supplier_nom
        FROM equipement_components ec
        LEFT JOIN stock_suppliers s ON s.id = ec.supplier_id
        WHERE ec.equipement_id = ?
        ORDER BY COALESCE(ec.parent_id, 0), ec.ordre, ec.nom, ec.id
        """,
        (equipment_id,),
    )
    elements = [dict(row) for row in cursor.fetchall()]

    cursor.execute(
        """
        SELECT id, nom
        FROM stock_suppliers
        WHERE actif = TRUE
        ORDER BY nom, id
        """
    )
    suppliers = [dict(row) for row in cursor.fetchall()]
    return elements, suppliers


@machine_structure_bp.route("/api/equipements/<int:equipment_id>/structure-technique-v3", methods=["GET"])
def get_structure(equipment_id):
    auth = _require_auth()
    if auth:
        return auth
    conn = get_db_connection()
    if not _equipment_exists(conn, equipment_id):
        conn.close()
        return jsonify({"error": "Équipement introuvable"}), 404
    if not _schema_ready(conn):
        conn.close()
        return jsonify({"schema_ready": False, "migration_required": MIGRATION_REVISION, "elements": [], "suppliers": []})
    elements, suppliers = _load_structure(conn, equipment_id)
    conn.close()
    return jsonify({
        "schema_ready": True,
        "migration_required": None,
        "elements": elements,
        "suppliers": suppliers,
    })


@machine_structure_bp.route("/api/equipements/<int:equipment_id>/structure-technique-v3", methods=["POST"])
def create_structure_element(equipment_id):
    denied = _require_editor()
    if denied:
        return denied

    payload = request.get_json(silent=True) or {}
    niveau = str(payload.get("niveau") or "").strip().lower()
    nom = str(payload.get("nom") or "").strip()
    code = str(payload.get("code") or "").strip() or None
    if niveau not in {"ensemble", "sous_ensemble", "composant"}:
        return jsonify({"error": "Niveau de structure invalide"}), 400
    if not nom:
        return jsonify({"error": "Le nom est obligatoire"}), 400

    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close()
        return _migration_required()
    if not _equipment_exists(conn, equipment_id):
        conn.close()
        return jsonify({"error": "Équipement introuvable"}), 404

    parent_id = payload.get("parent_id") or None
    if parent_id not in (None, ""):
        try:
            parent_id = int(parent_id)
        except (TypeError, ValueError):
            conn.close()
            return jsonify({"error": "Parent invalide"}), 400

    expected_parent_depth = {"ensemble": -1, "sous_ensemble": 0, "composant": 1}[niveau]
    actual_parent_depth = -1 if not parent_id else _component_depth(conn, equipment_id, parent_id)
    if actual_parent_depth is None or actual_parent_depth != expected_parent_depth:
        conn.close()
        labels = {
            "ensemble": "Un ensemble doit être créé à la racine de la machine.",
            "sous_ensemble": "Un sous-ensemble doit appartenir à un ensemble.",
            "composant": "Un composant doit appartenir à un sous-ensemble.",
        }
        return jsonify({"error": labels[niveau]}), 400

    try:
        supplier_id = _supplier_id(conn, payload.get("supplier_id")) if niveau == "composant" else None
        delai = _non_negative_int(payload.get("delai_obtention_jours"), "Délai d'obtention") if niveau == "composant" else None
        prix = _non_negative_decimal(payload.get("prix_unitaire"), "Prix") if niveau == "composant" else None
    except ValueError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 400

    type_composant = {
        "ensemble": "Ensemble",
        "sous_ensemble": "Sous-ensemble",
        "composant": "Composant",
    }[niveau]

    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO equipement_components
        (equipement_id, parent_id, code, nom, type_composant, criticite,
         supplier_id, delai_obtention_jours, prix_unitaire, ordre)
        VALUES (?, ?, ?, ?, ?, 'medium', ?, ?, ?, 0)
        RETURNING id
        """,
        (equipment_id, parent_id, code, nom, type_composant, supplier_id, delai, prix),
    )
    row_id = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "id": row_id}), 201


@machine_structure_bp.route("/api/equipements/<int:equipment_id>/structure-technique-v3/<int:element_id>", methods=["PATCH"])
def update_structure_element(equipment_id, element_id):
    denied = _require_editor()
    if denied:
        return denied

    payload = request.get_json(silent=True) or {}
    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close()
        return _migration_required()
    row = _component_row(conn, equipment_id, element_id)
    if not row:
        conn.close()
        return jsonify({"error": "Élément introuvable"}), 404

    depth = _component_depth(conn, equipment_id, element_id)
    if depth != 2:
        conn.close()
        return jsonify({"error": "Les données fournisseur, délai et prix sont réservées aux composants."}), 400

    nom = str(payload.get("nom") or row.get("nom") or "").strip()
    code = str(payload.get("code") or "").strip() or None
    if not nom:
        conn.close()
        return jsonify({"error": "Le nom du composant est obligatoire"}), 400
    try:
        supplier_id = _supplier_id(conn, payload.get("supplier_id"))
        delai = _non_negative_int(payload.get("delai_obtention_jours"), "Délai d'obtention")
        prix = _non_negative_decimal(payload.get("prix_unitaire"), "Prix")
    except ValueError as exc:
        conn.close()
        return jsonify({"error": str(exc)}), 400

    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE equipement_components
        SET nom = ?, code = ?, supplier_id = ?, delai_obtention_jours = ?,
            prix_unitaire = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND equipement_id = ?
        """,
        (nom, code, supplier_id, delai, prix, element_id, equipment_id),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@machine_structure_bp.route("/api/equipements/<int:equipment_id>/structure-technique-v3/<int:element_id>", methods=["DELETE"])
def delete_structure_element(equipment_id, element_id):
    denied = _require_editor()
    if denied:
        return denied

    conn = get_db_connection()
    if not _schema_ready(conn):
        conn.close()
        return _migration_required()
    row = _component_row(conn, equipment_id, element_id)
    if not row:
        conn.close()
        return jsonify({"error": "Élément introuvable"}), 404

    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM equipement_components WHERE parent_id = ? LIMIT 1", (element_id,))
    if cursor.fetchone():
        conn.close()
        return jsonify({"error": "Cet élément contient encore des éléments enfants. Supprimez-les d'abord."}), 409

    cursor.execute("DELETE FROM equipement_components WHERE id = ? AND equipement_id = ?", (element_id, equipment_id))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def register_machine_structure(app):
    if machine_structure_bp.name not in app.blueprints:
        app.register_blueprint(machine_structure_bp)
