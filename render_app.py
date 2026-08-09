"""Point d'entrée Render de la GMAO PostgreSQL.

Utiliser sur Render :
    python render_app.py

L'application principale ``app.py`` utilise directement PostgreSQL via
SQLAlchemy et ``database_compat.py``. Render fournit généralement une URL
``postgresql://...`` ; SQLAlchemy l'interprète alors avec le pilote psycopg2.
Le projet utilise psycopg 3, donc l'URL est normalisée avant d'importer app.py.
"""

import os


def _use_psycopg3_driver() -> None:
    database_url = (os.environ.get("DATABASE_URL") or "").strip()
    if database_url.startswith("postgresql://"):
        os.environ["DATABASE_URL"] = (
            "postgresql+psycopg://" + database_url[len("postgresql://") :]
        )
    elif database_url.startswith("postgres://"):
        os.environ["DATABASE_URL"] = (
            "postgresql+psycopg://" + database_url[len("postgres://") :]
        )


_use_psycopg3_driver()

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
