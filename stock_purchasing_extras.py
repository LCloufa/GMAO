from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from functools import wraps

from flask import redirect, render_template, request, session

from database_compat import get_db_connection
from manager_features import MANAGER_SAFE_ENDPOINTS


MANAGER_SAFE_ENDPOINTS.add("manager_purchases")


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").lower() != "admin":
            return "Accès refusé", 403
        return view(*args, **kwargs)
    return wrapped


def _manager_read_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").lower() not in {"manager", "admin"}:
            return "Accès refusé", 403
        return view(*args, **kwargs)
    return wrapped


def _next_order_number(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(id),0)+1 FROM stock_purchase_orders")
    return f"BC-{date.today().year}-{int(cursor.fetchone()[0] or 1):04d}"


def register_stock_purchasing_extras(app):
    @app.route("/stock/demandes-achat/<int:request_id>/create-order", methods=["POST"])
    @_admin_required
    def stock_purchase_request_create_order(request_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT r.article_id, r.requested_description, r.quantity, r.status,
                   ass.supplier_id, ass.reference_fournisseur, ass.prix, ass.delai_jours,
                   a.prix_unitaire
            FROM stock_purchase_requests r
            LEFT JOIN stock_articles a ON a.id=r.article_id
            LEFT JOIN stock_article_suppliers ass ON ass.article_id=r.article_id AND ass.prefere=TRUE
            WHERE r.id=?
            """,
            (request_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return "Demande introuvable", 404
        if row[3] != "approved":
            conn.close()
            return "La demande doit être approuvée avant création du bon.", 409
        if not row[0]:
            conn.close()
            return "Cette demande concerne une pièce non référencée. Crée d'abord l'article dans le stock.", 409
        if not row[4]:
            conn.close()
            return "Aucun fournisseur préféré n'est défini pour cet article.", 409

        number = _next_order_number(conn)
        cursor.execute(
            """
            INSERT INTO stock_purchase_orders
            (order_number,supplier_id,status,order_date,created_by_user_id,created_at,notes)
            VALUES (?,?,'draft',?,?,CURRENT_TIMESTAMP,?)
            """,
            (number, row[4], date.today(), session.get("user_id"), f"Créé depuis la demande d'achat #{request_id}"),
        )
        order_id = cursor.lastrowid
        unit_price = row[6] if row[6] is not None else (row[8] or 0)
        cursor.execute(
            """
            INSERT INTO stock_purchase_order_lines
            (purchase_order_id,article_id,supplier_reference,description,quantity_ordered,
             quantity_received,unit_price_ht,discount_percent,vat_percent,expected_lead_days)
            VALUES (?,?,?,?,?,0,?,0,20,?)
            """,
            (order_id, row[0], row[5], row[1], row[2], unit_price, row[7]),
        )
        cursor.execute(
            "UPDATE stock_purchase_requests SET status='ordered', purchase_order_id=? WHERE id=?",
            (order_id, request_id),
        )
        conn.commit()
        conn.close()
        return redirect(f"/stock/achats/{order_id}")

    @app.route("/manager/achats")
    @_manager_read_required
    def manager_purchases():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT o.id, o.order_number, s.nom, o.status, o.order_date,
                   o.desired_delivery_date,
                   COALESCE(SUM(l.quantity_ordered*l.unit_price_ht*(1-l.discount_percent/100.0)),0) AS amount,
                   COALESCE(SUM(l.quantity_ordered-l.quantity_received),0) AS remaining
            FROM stock_purchase_orders o
            JOIN stock_suppliers s ON s.id=o.supplier_id
            LEFT JOIN stock_purchase_order_lines l ON l.purchase_order_id=o.id
            GROUP BY o.id,o.order_number,s.nom,o.status,o.order_date,o.desired_delivery_date
            ORDER BY o.id DESC
            LIMIT 250
            """
        )
        orders = cursor.fetchall()

        active_statuses = {"approved", "ordered", "partially_received"}
        open_orders = sum(1 for o in orders if o[3] in active_statuses)
        late_orders = sum(
            1 for o in orders
            if o[3] in active_statuses and o[5] and str(o[5]) < date.today().isoformat()
        )
        committed = sum(float(o[6] or 0) for o in orders if o[3] not in {"draft", "pending_approval", "cancelled"})

        cursor.execute(
            """
            SELECT s.nom,
                   COALESCE(SUM(l.quantity_ordered*l.unit_price_ht*(1-l.discount_percent/100.0)),0) AS amount,
                   COUNT(DISTINCT o.id) AS orders
            FROM stock_suppliers s
            JOIN stock_purchase_orders o ON o.supplier_id=s.id
            JOIN stock_purchase_order_lines l ON l.purchase_order_id=o.id
            WHERE o.status NOT IN ('draft','pending_approval','cancelled')
            GROUP BY s.id,s.nom
            ORDER BY amount DESC
            LIMIT 10
            """
        )
        suppliers = cursor.fetchall()

        cursor.execute(
            """
            SELECT o.order_date, MAX(r.received_at)
            FROM stock_purchase_orders o
            JOIN stock_purchase_receipts r ON r.purchase_order_id=o.id
            WHERE o.status IN ('received','closed')
            GROUP BY o.id,o.order_date
            """
        )
        lead_days = []
        for ordered, received in cursor.fetchall():
            try:
                ordered_date = ordered if hasattr(ordered, "year") else date.fromisoformat(str(ordered))
                received_dt = received if isinstance(received, datetime) else datetime.fromisoformat(str(received))
                lead_days.append((received_dt.date() - ordered_date).days)
            except (TypeError, ValueError):
                pass
        avg_lead = round(sum(lead_days) / len(lead_days), 1) if lead_days else None

        cursor.execute("SELECT COUNT(*) FROM stock_purchase_requests WHERE status IN ('requested','approved')")
        pending_requests = int(cursor.fetchone()[0] or 0)
        conn.close()

        return render_template(
            "manager_purchases.html",
            orders=orders,
            suppliers=suppliers,
            open_orders=open_orders,
            late_orders=late_orders,
            committed=committed,
            avg_lead=avg_lead,
            pending_requests=pending_requests,
            alert_count=0,
        )
