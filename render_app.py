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
from machine_dossier import register_machine_dossier
from machine_dossier_runtime import patch_machine_dossier_runtime
from machine_structure import register_machine_structure
from machine_part_links import register_machine_part_links
from manager_features import register_manager_features
from manager_registration import register_account_role_creation
from stock_purchasing import register_stock_purchasing
from stock_purchasing_extras import register_stock_purchasing_extras
from stock_supplier_views import register_stock_supplier_views
from stock_reset import register_stock_reset


# Applique les garde-fous de compatibilité avant l'enregistrement du blueprint.
patch_machine_dossier_runtime()

# Enregistre les modules complémentaires avant le premier appel HTTP.
register_machine_dossier(app)
register_machine_structure(app)
register_machine_part_links(app)
register_manager_features(app)
register_account_role_creation(app)
register_stock_purchasing(app)
register_stock_purchasing_extras(app)
register_stock_supplier_views(app)
register_stock_reset(app)

# Prépare les dossiers d'upload. Le create_all historique reste temporairement
# conservé tant que toutes les tables des modules annexes n'ont pas leur propre
# migration Alembic. Les nouvelles tables Dossier Machine ne sont pas déclarées
# comme modèles SQLAlchemy et ne peuvent donc être créées que par leur migration.
ensure_upload_dirs()
init_db()

application = app


if __name__ == "__main__":
    application.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 10000)),
    )
