from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
BASE_PATH = ROOT / "templates" / "base.html"
DECLARATION_PATH = ROOT / "templates" / "nouvelle_declaration.html"

APP_BACKUP = APP_PATH.with_name("app_before_mobile_pwa.py")
BASE_BACKUP = BASE_PATH.with_name("base_before_mobile_pwa.html")
DECLARATION_BACKUP = DECLARATION_PATH.with_name("nouvelle_declaration_before_mobile_pwa.html")

APP_BEGIN = "# BEGIN MOBILE_PWA_SERVER"
APP_END = "# END MOBILE_PWA_SERVER"
HEAD_BEGIN = "<!-- BEGIN MOBILE_PWA_HEAD -->"
HEAD_END = "<!-- END MOBILE_PWA_HEAD -->"
NAV_BEGIN = "<!-- BEGIN MOBILE_PWA_NAV -->"
NAV_END = "<!-- END MOBILE_PWA_NAV -->"
SCRIPT_BEGIN = "<!-- BEGIN MOBILE_PWA_SCRIPT -->"
SCRIPT_END = "<!-- END MOBILE_PWA_SCRIPT -->"
CAMERA_BEGIN = "<!-- BEGIN MOBILE_CAMERA_UPLOAD -->"
CAMERA_END = "<!-- END MOBILE_CAMERA_UPLOAD -->"

HEAD_BLOCK = r'''
    <!-- BEGIN MOBILE_PWA_HEAD -->
    <meta name="theme-color" content="#2563eb">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="GMAO Pro">
    <link rel="manifest" href="/static/manifest.webmanifest">
    <link rel="icon" href="/static/pwa-icon.svg" type="image/svg+xml">
    <link rel="apple-touch-icon" href="/static/pwa-icon.svg">
    <link rel="stylesheet" href="/static/mobile-pwa.css">
    <!-- END MOBILE_PWA_HEAD -->
'''

NAV_BLOCK = r'''
<!-- BEGIN MOBILE_PWA_NAV -->
{% if 'user_id' in session %}
<nav class="mobile-bottom-nav" aria-label="Navigation mobile">
    <a href="/" class="{% if request.path == '/' %}active{% endif %}">
        <span class="mobile-nav-icon">🏠</span><span class="mobile-nav-label">Accueil</span>
    </a>
    <a href="/declarations" class="{% if request.path.startswith('/declarations') %}active{% endif %}">
        <span class="mobile-nav-icon">⚠️</span><span class="mobile-nav-label">Pannes</span>
    </a>

    {% if session.get('role') != 'operator' %}
    <a href="/interventions" class="{% if request.path.startswith('/interventions') %}active{% endif %}">
        <span class="mobile-nav-icon">🔧</span><span class="mobile-nav-label">Interv.</span>
    </a>
    <a href="/stock" class="{% if request.path.startswith('/stock') %}active{% endif %}">
        <span class="mobile-nav-icon">📦</span><span class="mobile-nav-label">Stock</span>
    </a>
    {% endif %}

    <button type="button" onclick="toggleMobileMore()" aria-label="Plus de navigation">
        <span class="mobile-nav-icon">☰</span><span class="mobile-nav-label">Plus</span>
    </button>
</nav>

<div id="mobileMoreBackdrop" class="mobile-more-backdrop" onclick="closeMobileMore()"></div>
<aside id="mobileMoreSheet" class="mobile-more-sheet" aria-label="Menu mobile complémentaire">
    <div class="mobile-more-title">
        <strong>GMAO Pro</strong>
        <button type="button" class="mobile-more-close" onclick="closeMobileMore()" aria-label="Fermer">✕</button>
    </div>

    {% if session.get('role') != 'operator' %}
    <a href="/equipements" class="{% if request.path.startswith('/equipements') %}active{% endif %}">⚙️ Équipements</a>
    <a href="/rapports" class="{% if request.path.startswith('/rapports') %}active{% endif %}">📝 Rapports</a>
    <a href="/techniciens" class="{% if request.path.startswith('/techniciens') %}active{% endif %}">🧰 Techniciens</a>
    <a href="/clients" class="{% if request.path.startswith('/clients') %}active{% endif %}">👥 Clients</a>
    {% endif %}

    {% if session.get('role') == 'admin' %}
    <a href="/users" class="{% if request.path.startswith('/users') %}active{% endif %}">🔐 Utilisateurs</a>
    {% endif %}

    <button id="pwaInstallButton" class="pwa-install-action" type="button" onclick="installGmaoPwa()" hidden>📲 Installer GMAO Pro</button>
    <p id="pwaIosInstallHint" class="pwa-ios-hint" hidden>Sur iPhone/iPad : touchez <strong>Partager</strong>, puis <strong>Sur l’écran d’accueil</strong>.</p>
    <a href="/logout" class="mobile-logout">↪ Déconnexion</a>
</aside>
{% endif %}
<!-- END MOBILE_PWA_NAV -->
'''

SCRIPT_BLOCK = r'''
<!-- BEGIN MOBILE_PWA_SCRIPT -->
<script src="/static/mobile-pwa.js"></script>
<!-- END MOBILE_PWA_SCRIPT -->
'''

APP_BLOCK = r'''
# BEGIN MOBILE_PWA_SERVER
@app.after_request
def configure_mobile_pwa(response):
    # Le service worker est servi depuis /static, mais doit pouvoir contrôler
    # toute l'application. Ce header autorise explicitement le scope racine.
    if request.path == "/static/service-worker.js":
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
    return response
# END MOBILE_PWA_SERVER

'''

OLD_CAMERA_BLOCK = r'''    <div style="margin-top:10px;">
      <label class="upload-btn">
        📷 Ajouter des photos (optionnel)
        <input type="file" name="photos" multiple accept="image/*" hidden>
      </label>
      <div class="file-name">Vous pouvez sélectionner plusieurs images</div>
    </div>'''

NEW_CAMERA_BLOCK = r'''    <!-- BEGIN MOBILE_CAMERA_UPLOAD -->
    <div style="margin-top:10px;">
      <div class="mobile-camera-actions">
        <label class="upload-btn">
          📷 Prendre une photo
          <input type="file" name="photos" accept="image/*" capture="environment" hidden>
        </label>
        <label class="upload-btn">
          🖼️ Choisir des images
          <input type="file" name="photos" multiple accept="image/*" hidden>
        </label>
      </div>
      <div class="file-name">Photos optionnelles · appareil photo ou galerie</div>
    </div>
    <!-- END MOBILE_CAMERA_UPLOAD -->'''


def patch_base(text: str) -> str:
    if HEAD_BEGIN not in text:
        anchor = "    <title>GMAO Pro</title>\n"
        if anchor not in text:
            raise RuntimeError("Balise <title> attendue introuvable dans templates/base.html.")
        text = text.replace(anchor, anchor + HEAD_BLOCK, 1)

    if NAV_BEGIN not in text:
        anchor = '<div class="tabs-page-layout">'
        if anchor not in text:
            raise RuntimeError("Point d'insertion de la navigation mobile introuvable dans templates/base.html.")
        text = text.replace(anchor, NAV_BLOCK + "\n" + anchor, 1)

    if SCRIPT_BEGIN not in text:
        anchor = "</body>"
        if anchor not in text:
            raise RuntimeError("Balise </body> introuvable dans templates/base.html.")
        text = text.replace(anchor, SCRIPT_BLOCK + "\n" + anchor, 1)

    return text


def patch_app(text: str) -> str:
    if APP_BEGIN in text and APP_END in text:
        return text

    if "request" not in text:
        raise RuntimeError("app.py ne semble pas importer/utiliser flask.request.")

    markers = [
        "# ==========================\n# Lancement",
        'if __name__ == "__main__":',
    ]
    insert_at = -1
    for marker in markers:
        pos = text.find(marker)
        if pos != -1:
            insert_at = pos
            break
    if insert_at == -1:
        raise RuntimeError("Point d'insertion avant le lancement Flask introuvable.")

    return text[:insert_at] + APP_BLOCK + text[insert_at:]


def patch_declaration(text: str) -> str:
    if CAMERA_BEGIN in text and CAMERA_END in text:
        return text
    if OLD_CAMERA_BLOCK not in text:
        raise RuntimeError("Bloc photo attendu introuvable dans nouvelle_declaration.html.")
    return text.replace(OLD_CAMERA_BLOCK, NEW_CAMERA_BLOCK, 1)


def main() -> int:
    for path in (APP_PATH, BASE_PATH, DECLARATION_PATH):
        if not path.exists():
            print(f"ERREUR : {path} introuvable")
            return 1

    app_text = APP_PATH.read_text(encoding="utf-8")
    base_text = BASE_PATH.read_text(encoding="utf-8")
    declaration_text = DECLARATION_PATH.read_text(encoding="utf-8")

    new_app = patch_app(app_text)
    new_base = patch_base(base_text)
    new_declaration = patch_declaration(declaration_text)

    if new_app == app_text and new_base == base_text and new_declaration == declaration_text:
        print("La version mobile/PWA est déjà installée.")
        return 0

    if new_app != app_text and not APP_BACKUP.exists():
        shutil.copy2(APP_PATH, APP_BACKUP)
        print(f"Sauvegarde créée : {APP_BACKUP.name}")
    if new_base != base_text and not BASE_BACKUP.exists():
        shutil.copy2(BASE_PATH, BASE_BACKUP)
        print(f"Sauvegarde créée : {BASE_BACKUP.name}")
    if new_declaration != declaration_text and not DECLARATION_BACKUP.exists():
        shutil.copy2(DECLARATION_PATH, DECLARATION_BACKUP)
        print(f"Sauvegarde créée : {DECLARATION_BACKUP.name}")

    APP_PATH.write_text(new_app, encoding="utf-8")
    BASE_PATH.write_text(new_base, encoding="utf-8")
    DECLARATION_PATH.write_text(new_declaration, encoding="utf-8")

    print("Version mobile/PWA installée.")
    print("- interface responsive sur téléphone")
    print("- barre de navigation tactile en bas")
    print("- manifeste PWA et mode application installable")
    print("- service worker limité aux fichiers statiques, sans cache des données métier")
    print("- prise de photo directe lors d'une déclaration de panne")
    print("Aucune migration PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERREUR : {exc}")
        sys.exit(1)
