"""Point d'entrée Render de la GMAO PostgreSQL.

Utiliser sur Render :
    python render_app.py

L'application principale ``app.py`` utilise désormais directement PostgreSQL
via SQLAlchemy et ``database_compat.py``. Aucun adaptateur SQLite n'est donc
nécessaire ici.
"""

import os

from app import app, ensure_upload_dirs, init_db


# Prépare les dossiers d'upload et crée uniquement les tables manquantes.
# Ces opérations sont idempotentes et ne suppriment aucune donnée existante.
ensure_upload_dirs()
init_db()

application = app


if __name__ == "__main__":
    application.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
