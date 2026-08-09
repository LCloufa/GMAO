"""Réinitialisation complète du périmètre Stock / Achats.

Ce module intercepte uniquement la cible ``stock`` de la route admin existante
``/admin/reset-data``. Les autres cibles continuent d'être traitées par
``app.reset_selected_data``.
"""

from flask import redirect, request, session

from database_compat import get_db_connection


STOCK_RESET_TABLES = (
    # Achats / approvisionnement : enfants avant parents.
    "stock_purchase_receipts",
    "stock_purchase_requests",
    "stock_purchase_order_lines",
    "stock_purchase_orders",
    "stock_supplier_profiles",
    # Stock utilisé par les interventions.
    "intervention_stock_items",
    "stock_reservations",
    "stock_movements",
    # Référentiel stock.
    "stock_article_suppliers",
    "stock_articles",
    "stock_categories",
    "stock_locations",
    "stock_suppliers",
)


def register_stock_reset(app):
    """Ajoute la cible ``stock`` à la réinitialisation sélective admin.

    Toute la suppression est réalisée dans une transaction unique. Une erreur
    sur une table provoque un rollback complet afin d'éviter un stock seulement
    partiellement réinitialisé.
    """

    @app.before_request
    def reset_complete_stock_scope():
        if request.method != "POST" or request.path != "/admin/reset-data":
            return None

        target = str(request.form.get("reset_target") or "").strip().lower()
        if target != "stock":
            return None

        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").strip().lower() != "admin":
            return "Accès refusé", 403

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            for table_name in STOCK_RESET_TABLES:
                cursor.execute(f"DELETE FROM {table_name}")
            conn.commit()
        except Exception as exc:
            conn.rollback()
            conn.close()
            print(f"Erreur réinitialisation complète du stock: {exc}")
            return (
                "La réinitialisation du stock a échoué. "
                "Aucune suppression partielle n'a été validée.",
                500,
            )

        conn.close()

        # Ce marqueur de session concerne la vérification des pièces avant
        # rapport. On le vide pour la session admin qui vient de remettre le
        # stock à zéro.
        session.pop("stock_reviewed_interventions", None)
        session.modified = True

        return redirect("/users?reset_done=stock")
