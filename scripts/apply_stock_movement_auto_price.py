from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[1]
APP_PATH = ROOT / "app.py"
TEMPLATE_PATH = ROOT / "templates" / "stock.html"
APP_BACKUP = ROOT / "app_before_stock_movement_auto_price.py"
TEMPLATE_BACKUP = ROOT / "templates" / "stock_before_movement_auto_price.html"

APP_BEGIN = "# BEGIN STOCK_MOVEMENT_AUTO_PRICE"
APP_END = "# END STOCK_MOVEMENT_AUTO_PRICE"
TEMPLATE_MARKER = "<!-- STOCK_MOVEMENT_AUTO_PRICE -->"


def patch_app() -> None:
    text = APP_PATH.read_text(encoding="utf-8")
    if APP_BEGIN in text:
        print("Calcul serveur des prix de mouvement déjà installé.")
        return

    anchor = '''    if delta < 0 and Decimal(str(article["stock_physique"])) + delta < 0:\n        conn.close()\n        return redirect("/stock?section=mouvements&error=insufficient")\n\n    cursor = conn.cursor()'''
    replacement = '''    if delta < 0 and Decimal(str(article["stock_physique"])) + delta < 0:\n        conn.close()\n        return redirect("/stock?section=mouvements&error=insufficient")\n\n    # BEGIN STOCK_MOVEMENT_AUTO_PRICE\n    # Le prix unitaire du mouvement reprend par défaut celui de la fiche article.\n    # Une valeur explicitement saisie reste possible et le total est dérivable\n    # par abs(quantite_delta) * prix_unitaire.\n    prix_saisi = request.form.get("prix_unitaire", "").strip()\n    if prix_saisi:\n        movement_unit_price = max(Decimal("0"), _stock_decimal(prix_saisi))\n    else:\n        movement_unit_price = max(\n            Decimal("0"),\n            _stock_decimal(article.get("prix_unitaire", 0)),\n        )\n    # END STOCK_MOVEMENT_AUTO_PRICE\n\n    cursor = conn.cursor()'''

    if anchor not in text:
        raise RuntimeError("Point d'insertion du prix automatique introuvable dans app.py")

    old_value = '''            _stock_decimal(request.form.get("prix_unitaire")) if request.form.get("prix_unitaire", "").strip() else None,'''
    if old_value not in text:
        raise RuntimeError("Valeur prix_unitaire historique introuvable dans app.py")

    if not APP_BACKUP.exists():
        shutil.copy2(APP_PATH, APP_BACKUP)
        print(f"Sauvegarde créée : {APP_BACKUP.name}")

    text = text.replace(anchor, replacement, 1)
    text = text.replace(old_value, "            movement_unit_price,", 1)
    APP_PATH.write_text(text, encoding="utf-8")
    print("Calcul serveur du prix de mouvement installé.")


def patch_template() -> None:
    text = TEMPLATE_PATH.read_text(encoding="utf-8")
    if TEMPLATE_MARKER in text:
        print("Calcul visuel des prix de mouvement déjà installé.")
        return

    replacements = [
        (
            '<thead><tr><th>Date</th><th>Article</th><th>Type</th><th>Quantité</th><th>Prix</th><th>Motif</th><th>Utilisateur</th><th>Intervention</th></tr></thead>',
            '<thead><tr><th>Date</th><th>Article</th><th>Type</th><th>Quantité</th><th>Prix unitaire</th><th>Prix total</th><th>Motif</th><th>Utilisateur</th><th>Intervention</th></tr></thead>',
        ),
        (
            '<td>{{ m[6] or \'-\' }}</td>\n                        <td>{{ m[7] or \'-\' }}</td>',
            '<td>{% if m[6] is not none %}{{ "%.2f"|format(m[6]) }} €{% else %}-{% endif %}</td>\n                        <td>{% if m[6] is not none %}{{ "%.2f"|format((m[5]|abs) * m[6]) }} €{% else %}-{% endif %}</td>\n                        <td>{{ m[7] or \'-\' }}</td>',
        ),
        (
            '<tr><td colspan="8" class="stock-empty">Aucun mouvement enregistré.</td></tr>',
            '<tr><td colspan="9" class="stock-empty">Aucun mouvement enregistré.</td></tr>',
        ),
        (
            '<option value="{{ a.id }}">{{ a.reference }} — {{ a.designation }} ({{ a.stock_physique }} {{ a.unite }})</option>',
            '<option value="{{ a.id }}" data-prix-unitaire="{{ a.prix_unitaire }}">{{ a.reference }} — {{ a.designation }} ({{ a.stock_physique }} {{ a.unite }})</option>',
        ),
        (
            '<div class="stock-form-group"><label>Quantité *</label><input type="number" step="0.001" min="0.001" name="quantite" required></div>\n        <div class="stock-form-group"><label>Prix unitaire (€)</label><input type="number" step="0.01" min="0" name="prix_unitaire"></div>',
            '<div class="stock-form-group"><label>Quantité *</label><input id="stockMovementQuantity" type="number" step="0.001" min="0.001" name="quantite" required></div>\n        <div class="stock-form-group"><label>Prix unitaire (€)</label><input id="stockMovementUnitPrice" type="number" step="0.01" min="0" name="prix_unitaire"></div>\n        <div class="stock-form-group full"><label>Prix total (€)</label><input id="stockMovementTotalPrice" type="text" value="0,00 €" readonly aria-readonly="true"><div class="stock-muted">Calcul automatique : quantité × prix unitaire.</div></div>',
        ),
        (
            '    document.getElementById("drawerBody").innerHTML = template.innerHTML;\n    document.getElementById("drawer").classList.add("open");',
            '    document.getElementById("drawerBody").innerHTML = template.innerHTML;\n    if (kind === "movement") initStockMovementPrice();\n    document.getElementById("drawer").classList.add("open");',
        ),
    ]

    for old, new in replacements:
        if old not in text:
            raise RuntimeError(f"Bloc attendu introuvable dans stock.html : {old[:80]}")
        text = text.replace(old, new, 1)

    script_anchor = '''function openStockDrawer(kind) {'''
    helper = '''// STOCK_MOVEMENT_AUTO_PRICE\nfunction initStockMovementPrice() {\n    const body = document.getElementById("drawerBody");\n    const form = body ? body.querySelector('form[action="/stock/mouvements/add"]') : null;\n    if (!form) return;\n\n    const articleSelect = form.querySelector('[name="article_id"]');\n    const quantityInput = form.querySelector('[name="quantite"]');\n    const unitPriceInput = form.querySelector('[name="prix_unitaire"]');\n    const totalInput = form.querySelector('#stockMovementTotalPrice');\n    if (!articleSelect || !quantityInput || !unitPriceInput || !totalInput) return;\n\n    const updateTotal = () => {\n        const quantity = Number(String(quantityInput.value || "0").replace(",", "."));\n        const unitPrice = Number(String(unitPriceInput.value || "0").replace(",", "."));\n        const total = Number.isFinite(quantity) && Number.isFinite(unitPrice)\n            ? Math.max(0, quantity) * Math.max(0, unitPrice)\n            : 0;\n        totalInput.value = total.toLocaleString("fr-FR", {\n            minimumFractionDigits: 2,\n            maximumFractionDigits: 2,\n        }) + " €";\n    };\n\n    const loadArticlePrice = () => {\n        const option = articleSelect.options[articleSelect.selectedIndex];\n        if (option && option.dataset.prixUnitaire !== undefined && option.value) {\n            const price = Number(String(option.dataset.prixUnitaire || "0").replace(",", "."));\n            unitPriceInput.value = Number.isFinite(price) ? price.toFixed(2) : "0.00";\n        } else if (!option || !option.value) {\n            unitPriceInput.value = "";\n        }\n        updateTotal();\n    };\n\n    articleSelect.addEventListener("change", loadArticlePrice);\n    quantityInput.addEventListener("input", updateTotal);\n    unitPriceInput.addEventListener("input", updateTotal);\n    loadArticlePrice();\n}\n\n'''
    if script_anchor not in text:
        raise RuntimeError("Fonction openStockDrawer introuvable dans stock.html")
    text = text.replace(script_anchor, helper + script_anchor, 1)

    if not TEMPLATE_BACKUP.exists():
        shutil.copy2(TEMPLATE_PATH, TEMPLATE_BACKUP)
        print(f"Sauvegarde créée : {TEMPLATE_BACKUP.name}")

    TEMPLATE_PATH.write_text(text, encoding="utf-8")
    print("Calcul visuel du prix total installé dans stock.html.")


def main() -> int:
    if not APP_PATH.exists() or not TEMPLATE_PATH.exists():
        print("ERREUR : app.py ou templates/stock.html introuvable.")
        return 1
    try:
        patch_app()
        patch_template()
    except Exception as exc:
        print(f"ERREUR : {exc}")
        return 1

    print("Terminé : aucun changement de schéma PostgreSQL n'est nécessaire.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
