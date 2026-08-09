from __future__ import annotations

import os

from flask import redirect, render_template, request
from werkzeug.security import generate_password_hash

from database_compat import get_db_connection


def _configured_key(name: str) -> str:
    return (os.getenv(name) or "").strip()


def register_account_role_creation(app) -> None:
    """Étend la route /register avec le rôle Manager.

    La route Flask existe déjà dans app.py. On remplace uniquement sa fonction
    de vue afin de conserver la même URL, le même endpoint et le même formulaire.
    Les clés restent exclusivement dans les variables d'environnement.
    """

    access_keys = {
        "admin": _configured_key("ADMIN_ACCESS_KEY"),
        "manager": _configured_key("MANAGER_ACCESS_KEY"),
        "technician": _configured_key("TECH_ACCESS_KEY"),
        "operator": _configured_key("OPERATOR_ACCESS_KEY"),
    }

    manager_key = access_keys["manager"]
    if not manager_key:
        app.logger.warning(
            "MANAGER_ACCESS_KEY n'est pas définie : la création directe d'un compte Manager par clé est désactivée."
        )

    non_empty_keys = [value for value in access_keys.values() if value]
    if len(non_empty_keys) != len(set(non_empty_keys)):
        raise RuntimeError(
            "Les clés ADMIN_ACCESS_KEY, MANAGER_ACCESS_KEY, TECH_ACCESS_KEY et OPERATOR_ACCESS_KEY doivent être distinctes."
        )

    def register_with_all_roles():
        if request.method == "POST":
            username = str(request.form.get("username") or "").strip()
            password_raw = str(request.form.get("password") or "")
            access_key = str(request.form.get("access_key") or "").strip()

            if not username or not password_raw:
                return "Nom d'utilisateur et mot de passe obligatoires.", 400

            role = "operator"
            if access_keys["admin"] and access_key == access_keys["admin"]:
                role = "admin"
            elif access_keys["manager"] and access_key == access_keys["manager"]:
                role = "manager"
            elif access_keys["technician"] and access_key == access_keys["technician"]:
                role = "technician"
            elif access_keys["operator"] and access_key == access_keys["operator"]:
                role = "operator"

            password = generate_password_hash(password_raw)
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "INSERT INTO users (username, password, role) VALUES (?, ?, ?)",
                    (username, password, role),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                conn.close()
                return "Utilisateur déjà existant"

            conn.close()
            return redirect("/login")

        return render_template("register.html")

    # Le endpoint 'register' est déjà relié à /register dans app.py.
    app.view_functions["register"] = register_with_all_roles
