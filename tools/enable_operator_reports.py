from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
BACKUP_PATH = ROOT / "app.py.before_operator_reports.bak"


def main():
    if not APP_PATH.exists():
        print(f"ERREUR : {APP_PATH} est introuvable.")
        return 1

    data = APP_PATH.read_bytes()

    variants = [
        (
            b'@role_required("admin", "technician")\r\ndef add_rapport():',
            b'@role_required("admin", "technician", "operator")\r\ndef add_rapport():',
        ),
        (
            b'@role_required("admin", "technician")\ndef add_rapport():',
            b'@role_required("admin", "technician", "operator")\ndef add_rapport():',
        ),
    ]

    if (
        b'@role_required("admin", "technician", "operator")\r\ndef add_rapport():' in data
        or b'@role_required("admin", "technician", "operator")\ndef add_rapport():' in data
    ):
        print("OK : le rôle operator est déjà autorisé à soumettre des rapports.")
        return 0

    for old, new in variants:
        if old in data:
            if not BACKUP_PATH.exists():
                shutil.copy2(APP_PATH, BACKUP_PATH)
                print(f"Sauvegarde créée : {BACKUP_PATH.name}")

            APP_PATH.write_bytes(data.replace(old, new, 1))
            print("OK : app.py a été modifié uniquement sur la permission de /rapports/add.")
            print('Nouvelle règle : @role_required("admin", "technician", "operator")')
            return 0

    print("ERREUR : la route add_rapport() n'a pas été trouvée sous la forme attendue.")
    print("Aucune modification n'a été effectuée.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
