from functools import wraps
from flask import redirect, render_template, session
from database_compat import get_db_connection


def staff_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect("/login")
        if str(session.get("role") or "").lower() not in {"admin", "technician"}:
            return "Accès refusé", 403
        return view(*args, **kwargs)
    return wrapped


def register_stock_supplier_views(app):
    @app.route("/stock/fournisseurs/infos-achats")
    @staff_required
    def stock_supplier_purchase_info():
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.id, s.nom, s.adresse, s.siret, s.contact_nom, s.contact_prenom,
                   s.telephone, s.email, s.site_web, s.actif,
                   p.code_fournisseur, p.conditions_paiement, p.delai_moyen_jours,
                   p.franco_port, p.adresse_livraison_defaut, p.notes_achats
            FROM stock_suppliers s
            LEFT JOIN stock_supplier_profiles p ON p.supplier_id=s.id
            ORDER BY s.nom ASC
        """)
        suppliers = cursor.fetchall()
        conn.close()
        return render_template("stock_supplier_purchase_info.html", suppliers=suppliers)
