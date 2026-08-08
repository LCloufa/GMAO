from pathlib import Path
import re
import shutil


APP_PATH = Path("app.py")
BACKUP_PATH = Path("app.sqlite_backup.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: attendu 1 occurrence, trouvé {count}")
    return text.replace(old, new, 1)


def main():
    if not APP_PATH.exists():
        raise FileNotFoundError("app.py introuvable dans le dossier courant")

    original = APP_PATH.read_text(encoding="utf-8")
    text = original

    if not BACKUP_PATH.exists():
        shutil.copy2(APP_PATH, BACKUP_PATH)
        print(f"Sauvegarde créée : {BACKUP_PATH}")
    else:
        print(f"Sauvegarde déjà présente : {BACKUP_PATH}")

    text = replace_once(
        text,
        "import sqlite3\n",
        "from dotenv import load_dotenv\n"
        "from flask_migrate import Migrate\n"
        "from models import db\n"
        "from database_compat import get_db_connection\n",
        "remplacement import sqlite3",
    )

    old_config = '''app = Flask(__name__)
app.secret_key = "cle_super_secrete_change_moi"
ADMIN_ACCESS_KEY = "GMAO-2026-SECURE"
OPERATOR_ACCESS_KEY = "GMAO-OP-2026"
TECH_ACCESS_KEY = "GMAO-TECH-2026"
'''

    new_config = '''load_dotenv()

app = Flask(__name__)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL est absent du fichier .env")

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)
migrate = Migrate(app, db)

app.secret_key = os.getenv("SECRET_KEY")
ADMIN_ACCESS_KEY = os.getenv("ADMIN_ACCESS_KEY")
OPERATOR_ACCESS_KEY = os.getenv("OPERATOR_ACCESS_KEY")
TECH_ACCESS_KEY = os.getenv("TECH_ACCESS_KEY")

if not all((app.secret_key, ADMIN_ACCESS_KEY, OPERATOR_ACCESS_KEY, TECH_ACCESS_KEY)):
    raise RuntimeError(
        "SECRET_KEY, ADMIN_ACCESS_KEY, OPERATOR_ACCESS_KEY et TECH_ACCESS_KEY "
        "doivent être définies dans .env"
    )
'''

    text = replace_once(text, old_config, new_config, "configuration Flask")

    # Remplace l'ancien init_db SQLite par la création/vérification du schéma SQLAlchemy.
    pattern = re.compile(
        r"def init_db\(\):\n.*?(?=\ndef sync_equipement_statut\()",
        flags=re.DOTALL,
    )
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"init_db: attendu 1 bloc, trouvé {len(matches)}")

    new_init = '''def init_db():
    """Crée les tables manquantes dans PostgreSQL sans supprimer les données."""
    with app.app_context():
        db.create_all()

'''
    text = pattern.sub(new_init, text, count=1)

    connection_count = text.count('sqlite3.connect("database.db")')
    if connection_count == 0:
        raise RuntimeError("Aucune connexion SQLite à convertir n'a été trouvée")
    text = text.replace('sqlite3.connect("database.db")', "get_db_connection()")

    text = text.replace("conn.row_factory = sqlite3.Row", "conn.row_factory = dict")

    # Correction d'un ancien bug dans /equipements/add si la version courante le contient.
    text = text.replace(
        'localisation = request.form["localisation"]',
        'emplacement = request.form["localisation"]',
    )

    if "sqlite3.connect" in text or "sqlite3.Row" in text:
        raise RuntimeError("Il reste des appels SQLite dans app.py après conversion")

    # Vérification syntaxique avant d'écrire le fichier.
    compile(text, str(APP_PATH), "exec")
    APP_PATH.write_text(text, encoding="utf-8")

    print(f"Conversion terminée : {connection_count} connexion(s) basculée(s) vers PostgreSQL")
    print("Vérification syntaxique : OK")
    print("L'ancien fichier reste disponible dans app.sqlite_backup.py")


if __name__ == "__main__":
    main()
