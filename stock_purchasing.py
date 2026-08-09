from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import wraps

from flask import redirect, render_template, request, send_file, session
from sqlalchemy import CheckConstraint, UniqueConstraint

from database_compat import get_db_connection
from models import db
from purchasing_pdf import create_purchase_order_pdf


ORDER_STATUSES = (
    "draft",
    "pending_approval",
    "approved",
    "ordered",
    "partially_received",
    "received",
    "closed",
    "cancelled",
)
REQUEST_STATUSES = ("requested", "approved", "ordered", "fulfilled", "cancelled")


class StockSupplierProfile(db.Model):
    __tablename__ = "stock_supplier_profiles"

    id = db.Column(db.Integer, primary_key=True)
    supplier_id = db.Column(db.Integer, db.ForeignKey("stock_suppliers.id", ondelete="CASCADE"), unique=True, nullable=False)
    code_fournisseur = db.Column(db.String(100))
    conditions_paiement = db.Column(db.String(255))
    delai_moyen_jours = db.Column(db.Integer)
    franco_port = db.Column(db.Numeric(14, 2))
    adresse_livraison_defaut = db.Column(db.Text)
    notes_achats = db.Column(db.Text)


class StockPurchaseOrder(db.Model):
    __tablename__ = "stock_purchase_orders"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','pending_approval','approved','ordered','partially_received','received','closed','cancelled')",
            name="ck_stock_purchase_orders_status",
        ),
        UniqueConstraint("order_number", name="uq_stock_purchase_orders_number"),
    )

    id = db.Column(db.Integer, primary_key=True)
    order_number = db.Column(db.String(50), nullable=False)
    supplier_id = db.Column(db.Integer, db.ForeignKey("stock_suppliers.id"), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="draft")
    order_date = db.Column(db.Date, nullable=False, default=date.today)
    desired_delivery_date = db.Column(db.Date)
    delivery_address = db.Column(db.Text)
    shipping_cost = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    global_discount_percent = db.Column(db.Numeric(8, 3), nullable=False, default=0)
    notes = db.Column(db.Text)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    approved_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime)


class StockPurchaseOrderLine(db.Model):
    __tablename__ = "stock_purchase_order_lines"

    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("stock_purchase_orders.id", ondelete="CASCADE"), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey("stock_articles.id"), nullable=False)
    supplier_reference = db.Column(db.String(255))
    description = db.Column(db.String(500), nullable=False)
    quantity_ordered = db.Column(db.Numeric(14, 3), nullable=False)
    quantity_received = db.Column(db.Numeric(14, 3), nullable=False, default=0)
    unit_price_ht = db.Column(db.Numeric(14, 2), nullable=False, default=0)
    discount_percent = db.Column(db.Numeric(8, 3), nullable=False, default=0)
    vat_percent = db.Column(db.Numeric(8, 3), nullable=False, default=20)
    expected_lead_days = db.Column(db.Integer)


class StockPurchaseReceipt(db.Model):
    __tablename__ = "stock_purchase_receipts"

    id = db.Column(db.Integer, primary_key=True)
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("stock_purchase_orders.id", ondelete="CASCADE"), nullable=False)
    purchase_order_line_id = db.Column(db.Integer, db.ForeignKey("stock_purchase_order_lines.id", ondelete="CASCADE"), nullable=False)
    quantity_received = db.Column(db.Numeric(14, 3), nullable=False)
    received_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    received_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    note = db.Column(db.Text)


class StockPurchaseRequest(db.Model):
    __tablename__ = "stock_purchase_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','approved','ordered','fulfilled','cancelled')",
            name="ck_stock_purchase_requests_status",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    intervention_id = db.Column(db.Integer, db.ForeignKey("interventions.id", ondelete="SET NULL"))
    article_id = db.Column(db.Integer, db.ForeignKey("stock_articles.id", ondelete="SET NULL"))
    requested_reference = db.Column(db.String(255))
    requested_description = db.Column(db.Text, nullable=False)
    quantity = db.Column(db.Numeric(14, 3), nullable=False, default=1)
    priority = db.Column(db.String(30), nullable=False, default="medium")
    status = db.Column(db.String(30), nullable=False, default="requested")
    requested_by_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    purchase_order_id = db.Column(db.Integer, db.ForeignKey("stock_purchase_orders.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


def _decimal(value, default="0"):
    try:
        return Decimal(str(value if value not in (None, "") else default).strip().replace(",", "."))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").lower() not in {"admin", "technician"}:
            return "Accès refusé", 403
        return view(*args, **kwargs)
    return wrapped


def _admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").lower() != "admin":
            return "Accès refusé", 403
        return view(*args, **kwargs)
    return wrapped


def _next_order_number(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM stock_purchase_orders")
    seq = int(cursor.fetchone()[0] or 1)
    return f"BC-{date.today().year}-{seq:04d}"


def _supplier_rows(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.id, s.nom, s.adresse, s.siret, s.contact_nom, s.contact_prenom,
               s.telephone, s.email, s.site_web, s.actif,
               p.code_fournisseur, p.conditions_paiement, p.delai_moyen_jours,
               p.franco_port, p.adresse_livraison_defaut
        FROM stock_suppliers s
        LEFT JOIN stock_supplier_profiles p ON p.supplier_id = s.id
        ORDER BY s.nom ASC
        """
    )
    return cursor.fetchall()


def _article_rows(conn):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.id, a.reference, a.designation, a.unite, a.stock_min, a.stock_max,
               a.prix_unitaire,
               COALESCE((SELECT SUM(m.quantite_delta) FROM stock_movements m WHERE m.article_id=a.id),0) AS physical,
               COALESCE((SELECT SUM(r.quantite-r.quantite_consommee) FROM stock_reservations r WHERE r.article_id=a.id AND r.statut='reserved'),0) AS reserved,
               COALESCE((SELECT SUM(l.quantity_ordered-l.quantity_received)
                         FROM stock_purchase_order_lines l
                         JOIN stock_purchase_orders o ON o.id=l.purchase_order_id
                         WHERE l.article_id=a.id AND o.status IN ('approved','ordered','partially_received')),0) AS on_order,
               ass.supplier_id, s.nom, ass.reference_fournisseur, ass.prix, ass.delai_jours, ass.prefere
        FROM stock_articles a
        LEFT JOIN stock_article_suppliers ass ON ass.article_id=a.id AND ass.prefere=TRUE
        LEFT JOIN stock_suppliers s ON s.id=ass.supplier_id
        WHERE a.actif=TRUE
        ORDER BY a.reference ASC
        """
    )
    items = []
    for row in cursor.fetchall():
        physical = _decimal(row[7])
        reserved = _decimal(row[8])
        on_order = _decimal(row[9])
        available = physical - reserved
        stock_min = _decimal(row[4])
        stock_max = _decimal(row[5]) if row[5] is not None else None
        target = stock_max if stock_max is not None and stock_max > 0 else stock_min * Decimal("2")
        recommended = max(Decimal("0"), target - available - on_order)
        items.append({
            "id": row[0], "reference": row[1], "designation": row[2], "unite": row[3],
            "stock_min": float(stock_min), "stock_max": float(stock_max) if stock_max is not None else None,
            "unit_price": float(_decimal(row[6])), "physical": float(physical), "reserved": float(reserved),
            "available": float(available), "on_order": float(on_order), "recommended": float(recommended),
            "supplier_id": row[10], "supplier_name": row[11], "supplier_reference": row[12],
            "supplier_price": float(_decimal(row[13])) if row[13] is not None else None,
            "lead_days": row[14], "preferred": bool(row[15]) if row[15] is not None else False,
            "needs_reorder": available <= stock_min,
        })
    return items


def _order_totals(lines, shipping_cost=0, global_discount_percent=0):
    subtotal = Decimal("0")
    vat = Decimal("0")
    for line in lines:
        qty = _decimal(line[4])
        price = _decimal(line[6])
        discount = _decimal(line[7])
        vat_percent = _decimal(line[8])
        net = qty * price * (Decimal("1") - discount / Decimal("100"))
        subtotal += net
        vat += net * vat_percent / Decimal("100")
    subtotal *= Decimal("1") - _decimal(global_discount_percent) / Decimal("100")
    shipping = _decimal(shipping_cost)
    return {
        "subtotal_ht": float(subtotal),
        "shipping_ht": float(shipping),
        "vat": float(vat),
        "total_ttc": float(subtotal + shipping + vat),
    }


def _order_detail(conn, order_id):
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT o.id, o.order_number, o.supplier_id, s.nom, o.status, o.order_date,
               o.desired_delivery_date, o.delivery_address, o.shipping_cost,
               o.global_discount_percent, o.notes, o.created_at,
               COALESCE(u.username,'-'), COALESCE(a.username,'-'),
               s.adresse, s.siret, s.email, s.telephone
        FROM stock_purchase_orders o
        JOIN stock_suppliers s ON s.id=o.supplier_id
        LEFT JOIN users u ON u.id=o.created_by_user_id
        LEFT JOIN users a ON a.id=o.approved_by_user_id
        WHERE o.id=?
        """,
        (order_id,),
    )
    order = cursor.fetchone()
    if not order:
        return None, [], {}
    cursor.execute(
        """
        SELECT l.id, a.reference, l.description, l.supplier_reference,
               l.quantity_ordered, l.quantity_received, l.unit_price_ht,
               l.discount_percent, l.vat_percent, l.expected_lead_days, l.article_id, a.unite
        FROM stock_purchase_order_lines l
        JOIN stock_articles a ON a.id=l.article_id
        WHERE l.purchase_order_id=?
        ORDER BY l.id ASC
        """,
        (order_id,),
    )
    lines = cursor.fetchall()
    totals = _order_totals(lines, order[8], order[9])
    return order, lines, totals


def register_stock_purchasing(app):
    @app.route("/stock/achats")
    @_staff_required
    def stock_purchases():
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
            GROUP BY o.id, o.order_number, s.nom, o.status, o.order_date, o.desired_delivery_date
            ORDER BY o.id DESC
            """
        )
        orders = cursor.fetchall()
        suppliers = [row for row in _supplier_rows(conn) if row[9]]
        conn.close()
        return render_template("stock_orders.html", orders=orders, suppliers=suppliers, statuses=ORDER_STATUSES)

    @app.route("/stock/achats/create", methods=["POST"])
    @_staff_required
    def stock_purchase_create():
        supplier_id = request.form.get("supplier_id")
        if not supplier_id:
            return "Fournisseur obligatoire", 400
        conn = get_db_connection()
        cursor = conn.cursor()
        number = _next_order_number(conn)
        cursor.execute(
            """
            INSERT INTO stock_purchase_orders
            (order_number, supplier_id, status, order_date, desired_delivery_date,
             delivery_address, shipping_cost, global_discount_percent, notes,
             created_by_user_id, created_at)
            VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (number, supplier_id, date.today(), request.form.get("desired_delivery_date") or None,
             request.form.get("delivery_address", "").strip() or None,
             max(Decimal("0"), _decimal(request.form.get("shipping_cost"))),
             max(Decimal("0"), _decimal(request.form.get("global_discount_percent"))),
             request.form.get("notes", "").strip() or None, session.get("user_id")),
        )
        order_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>")
    @_staff_required
    def stock_purchase_detail(order_id):
        conn = get_db_connection()
        order, lines, totals = _order_detail(conn, order_id)
        if not order:
            conn.close()
            return "Bon de commande introuvable", 404
        articles = _article_rows(conn)
        requests_cursor = conn.cursor()
        requests_cursor.execute(
            """
            SELECT r.id, r.requested_description, r.quantity, r.priority,
                   COALESCE(i.title,'-')
            FROM stock_purchase_requests r
            LEFT JOIN interventions i ON i.id=r.intervention_id
            WHERE r.purchase_order_id=?
            ORDER BY r.created_at DESC
            """,
            (order_id,),
        )
        linked_requests = requests_cursor.fetchall()
        conn.close()
        return render_template(
            "stock_order_detail.html", order=order, lines=lines, totals=totals,
            articles=articles, linked_requests=linked_requests, statuses=ORDER_STATUSES,
        )

    @app.route("/stock/achats/<int:order_id>/line", methods=["POST"])
    @_staff_required
    def stock_purchase_add_line(order_id):
        article_id = request.form.get("article_id")
        quantity = max(Decimal("0"), _decimal(request.form.get("quantity")))
        if not article_id or quantity <= 0:
            return "Article et quantité obligatoires", 400
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT status, supplier_id FROM stock_purchase_orders WHERE id=?", (order_id,))
        state = cursor.fetchone()
        if not state:
            conn.close(); return "Bon introuvable", 404
        if state[0] not in {"draft", "pending_approval"}:
            conn.close(); return "Ce bon n'est plus modifiable", 409
        cursor.execute("SELECT reference, designation, prix_unitaire FROM stock_articles WHERE id=?", (article_id,))
        article = cursor.fetchone()
        if not article:
            conn.close(); return "Article introuvable", 404
        cursor.execute(
            "SELECT reference_fournisseur, prix, delai_jours FROM stock_article_suppliers WHERE article_id=? AND supplier_id=? LIMIT 1",
            (article_id, state[1]),
        )
        supplier_info = cursor.fetchone()
        supplier_reference = request.form.get("supplier_reference", "").strip() or (supplier_info[0] if supplier_info else None)
        default_price = supplier_info[1] if supplier_info and supplier_info[1] is not None else article[2]
        default_lead = supplier_info[2] if supplier_info else None
        cursor.execute(
            """
            INSERT INTO stock_purchase_order_lines
            (purchase_order_id, article_id, supplier_reference, description,
             quantity_ordered, quantity_received, unit_price_ht, discount_percent,
             vat_percent, expected_lead_days)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            """,
            (order_id, article_id, supplier_reference, article[1], quantity,
             max(Decimal("0"), _decimal(request.form.get("unit_price"), str(default_price or 0))),
             max(Decimal("0"), _decimal(request.form.get("discount_percent"))),
             max(Decimal("0"), _decimal(request.form.get("vat_percent"), "20")),
             int(request.form.get("lead_days") or default_lead or 0) or None),
        )
        conn.commit(); conn.close()
        return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>/submit", methods=["POST"])
    @_staff_required
    def stock_purchase_submit(order_id):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stock_purchase_order_lines WHERE purchase_order_id=?", (order_id,))
        if int(cursor.fetchone()[0] or 0) == 0:
            conn.close(); return "Ajoute au moins une ligne avant validation", 409
        cursor.execute("UPDATE stock_purchase_orders SET status='pending_approval', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='draft'", (order_id,))
        conn.commit(); conn.close(); return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>/approve", methods=["POST"])
    @_admin_required
    def stock_purchase_approve(order_id):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE stock_purchase_orders SET status='approved', approved_by_user_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND status IN ('draft','pending_approval')", (session.get("user_id"), order_id))
        conn.commit(); conn.close(); return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>/mark-ordered", methods=["POST"])
    @_staff_required
    def stock_purchase_mark_ordered(order_id):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE stock_purchase_orders SET status='ordered', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='approved'", (order_id,))
        conn.commit(); conn.close(); return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>/cancel", methods=["POST"])
    @_admin_required
    def stock_purchase_cancel(order_id):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE stock_purchase_orders SET status='cancelled', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status NOT IN ('received','closed')", (order_id,))
        conn.commit(); conn.close(); return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>/receive/<int:line_id>", methods=["POST"])
    @_staff_required
    def stock_purchase_receive(order_id, line_id):
        quantity = max(Decimal("0"), _decimal(request.form.get("quantity")))
        if quantity <= 0:
            return "Quantité invalide", 400
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute(
            """
            SELECT l.article_id, l.quantity_ordered, l.quantity_received, l.unit_price_ht,
                   o.order_number, o.status
            FROM stock_purchase_order_lines l
            JOIN stock_purchase_orders o ON o.id=l.purchase_order_id
            WHERE l.id=? AND l.purchase_order_id=?
            """, (line_id, order_id))
        row = cursor.fetchone()
        if not row:
            conn.close(); return "Ligne introuvable", 404
        if row[5] not in {"approved", "ordered", "partially_received"}:
            conn.close(); return "La commande n'est pas réceptionnable", 409
        remaining = _decimal(row[1]) - _decimal(row[2])
        received = min(quantity, remaining)
        if received <= 0:
            conn.close(); return "Cette ligne est déjà entièrement reçue", 409
        cursor.execute("UPDATE stock_purchase_order_lines SET quantity_received=quantity_received+? WHERE id=?", (received, line_id))
        cursor.execute(
            "INSERT INTO stock_purchase_receipts (purchase_order_id,purchase_order_line_id,quantity_received,received_by_user_id,received_at,note) VALUES (?,?,?,?,CURRENT_TIMESTAMP,?)",
            (order_id, line_id, received, session.get("user_id"), request.form.get("note", "").strip() or None))
        cursor.execute(
            """
            INSERT INTO stock_movements
            (article_id,type_mouvement,quantite_delta,prix_unitaire,motif,created_by_user_id,created_at)
            VALUES (?, 'entree', ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (row[0], received, row[3], f"Réception fournisseur {row[4]}", session.get("user_id")))
        cursor.execute("SELECT COUNT(*) FROM stock_purchase_order_lines WHERE purchase_order_id=? AND quantity_received < quantity_ordered", (order_id,))
        remaining_lines = int(cursor.fetchone()[0] or 0)
        new_status = "received" if remaining_lines == 0 else "partially_received"
        cursor.execute("UPDATE stock_purchase_orders SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (new_status, order_id))
        cursor.execute("UPDATE stock_purchase_requests SET status='fulfilled' WHERE purchase_order_id=? AND ?='received'", (order_id, new_status))
        conn.commit(); conn.close(); return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>/close", methods=["POST"])
    @_admin_required
    def stock_purchase_close(order_id):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE stock_purchase_orders SET status='closed', updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='received'", (order_id,))
        conn.commit(); conn.close(); return redirect(f"/stock/achats/{order_id}")

    @app.route("/stock/achats/<int:order_id>/pdf")
    @_staff_required
    def stock_purchase_pdf(order_id):
        conn = get_db_connection(); order, lines, totals = _order_detail(conn, order_id); conn.close()
        if not order:
            return "Bon de commande introuvable", 404
        output = create_purchase_order_pdf(order, lines, totals)
        return send_file(output, as_attachment=True, download_name=f"{order[1]}.pdf", mimetype="application/pdf")

    @app.route("/stock/reapprovisionnement")
    @_staff_required
    def stock_replenishment():
        conn = get_db_connection(); items = [i for i in _article_rows(conn) if i["needs_reorder"]]; conn.close()
        return render_template("stock_replenishment.html", items=items)

    @app.route("/stock/reapprovisionnement/create-orders", methods=["POST"])
    @_staff_required
    def stock_replenishment_create_orders():
        selected = {int(v) for v in request.form.getlist("article_ids") if str(v).isdigit()}
        conn = get_db_connection(); items = [i for i in _article_rows(conn) if i["id"] in selected and i["recommended"] > 0]
        groups = {}
        for item in items:
            if not item["supplier_id"]:
                continue
            groups.setdefault(int(item["supplier_id"]), []).append(item)
        cursor = conn.cursor(); created = []
        for supplier_id, group in groups.items():
            number = _next_order_number(conn)
            cursor.execute("INSERT INTO stock_purchase_orders (order_number,supplier_id,status,order_date,created_by_user_id,created_at,notes) VALUES (?,?,'draft',?,?,CURRENT_TIMESTAMP,?)", (number, supplier_id, date.today(), session.get("user_id"), "Créée depuis le réapprovisionnement automatique"))
            order_id = cursor.lastrowid; created.append(order_id)
            for item in group:
                cursor.execute(
                    """INSERT INTO stock_purchase_order_lines
                    (purchase_order_id,article_id,supplier_reference,description,quantity_ordered,quantity_received,unit_price_ht,discount_percent,vat_percent,expected_lead_days)
                    VALUES (?,?,?,?,?,0,?,0,20,?)""",
                    (order_id, item["id"], item["supplier_reference"], item["designation"], Decimal(str(item["recommended"])), Decimal(str(item["supplier_price"] if item["supplier_price"] is not None else item["unit_price"])), item["lead_days"]))
        conn.commit(); conn.close()
        if len(created) == 1:
            return redirect(f"/stock/achats/{created[0]}")
        return redirect("/stock/achats")

    @app.route("/stock/demandes-achat")
    @_staff_required
    def stock_purchase_requests():
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute(
            """
            SELECT r.id, r.created_at, r.priority, r.status, r.requested_reference,
                   r.requested_description, r.quantity, COALESCE(i.title,'-'),
                   COALESCE(a.reference,'-'), COALESCE(u.username,'-'), r.purchase_order_id
            FROM stock_purchase_requests r
            LEFT JOIN interventions i ON i.id=r.intervention_id
            LEFT JOIN stock_articles a ON a.id=r.article_id
            LEFT JOIN users u ON u.id=r.requested_by_user_id
            ORDER BY r.created_at DESC
            """
        )
        rows = cursor.fetchall()
        cursor.execute("SELECT id,title FROM interventions WHERE status IN ('planned','in_progress') ORDER BY scheduled_date DESC")
        interventions = cursor.fetchall()
        articles = _article_rows(conn); conn.close()
        return render_template("stock_purchase_requests.html", requests=rows, interventions=interventions, articles=articles)

    @app.route("/stock/demandes-achat/create", methods=["POST"])
    @_staff_required
    def stock_purchase_request_create():
        description = request.form.get("description", "").strip()
        quantity = max(Decimal("0.001"), _decimal(request.form.get("quantity"), "1"))
        if not description:
            return "Description obligatoire", 400
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO stock_purchase_requests
            (intervention_id,article_id,requested_reference,requested_description,quantity,priority,status,requested_by_user_id,created_at)
            VALUES (?,?,?,?,?,?,'requested',?,CURRENT_TIMESTAMP)""",
            (request.form.get("intervention_id") or None, request.form.get("article_id") or None,
             request.form.get("reference", "").strip() or None, description, quantity,
             request.form.get("priority", "medium"), session.get("user_id")))
        conn.commit(); conn.close(); return redirect("/stock/demandes-achat")

    @app.route("/stock/demandes-achat/<int:request_id>/approve", methods=["POST"])
    @_admin_required
    def stock_purchase_request_approve(request_id):
        conn = get_db_connection(); cursor = conn.cursor(); cursor.execute("UPDATE stock_purchase_requests SET status='approved' WHERE id=? AND status='requested'", (request_id,)); conn.commit(); conn.close(); return redirect("/stock/demandes-achat")

    @app.route("/stock/fournisseurs/<int:supplier_id>/profile", methods=["POST"])
    @_admin_required
    def stock_supplier_profile_update(supplier_id):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("SELECT id FROM stock_supplier_profiles WHERE supplier_id=?", (supplier_id,)); existing = cursor.fetchone()
        values = (request.form.get("code_fournisseur", "").strip() or None,
                  request.form.get("conditions_paiement", "").strip() or None,
                  int(request.form.get("delai_moyen_jours") or 0) or None,
                  _decimal(request.form.get("franco_port")) if request.form.get("franco_port", "").strip() else None,
                  request.form.get("adresse_livraison_defaut", "").strip() or None,
                  request.form.get("notes_achats", "").strip() or None)
        if existing:
            cursor.execute("UPDATE stock_supplier_profiles SET code_fournisseur=?,conditions_paiement=?,delai_moyen_jours=?,franco_port=?,adresse_livraison_defaut=?,notes_achats=? WHERE supplier_id=?", (*values, supplier_id))
        else:
            cursor.execute("INSERT INTO stock_supplier_profiles (code_fournisseur,conditions_paiement,delai_moyen_jours,franco_port,adresse_livraison_defaut,notes_achats,supplier_id) VALUES (?,?,?,?,?,?,?)", (*values, supplier_id))
        conn.commit(); conn.close(); return redirect("/stock?section=fournisseurs")
