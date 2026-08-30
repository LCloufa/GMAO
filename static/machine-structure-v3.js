document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-machine-dossier]");
  const target = root?.querySelector("[data-machine-structure-v3]");
  if (!root || !target) return;

  const equipmentId = root.dataset.equipmentId;
  const canEdit = root.dataset.canEdit === "1";
  let state = { schema_ready: false, elements: [], suppliers: [] };

  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[char]));
  const money = (value) => value == null || value === ""
    ? "—"
    : new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 2 }).format(Number(value));

  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, {
      headers: { "Accept": "application/json", "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw new Error(data.error || `Erreur ${response.status}`);
    return data;
  }

  function byIdMap(items) {
    return new Map(items.map((item) => [Number(item.id), item]));
  }

  function depthOf(item, byId) {
    let depth = 0;
    let current = item;
    const seen = new Set();
    while (current?.parent_id) {
      const parentId = Number(current.parent_id);
      if (seen.has(parentId) || !byId.has(parentId)) return 99;
      seen.add(parentId);
      current = byId.get(parentId);
      depth += 1;
      if (depth > 20) return 99;
    }
    return depth;
  }

  function supplierOptions(selectedId = "") {
    const selected = Number(selectedId || 0);
    return '<option value="">Aucun fournisseur</option>' + (state.suppliers || []).map((supplier) =>
      `<option value="${Number(supplier.id)}" ${Number(supplier.id) === selected ? "selected" : ""}>${esc(supplier.nom)}</option>`
    ).join("");
  }

  function deleteButton(item, label) {
    if (!canEdit) return "";
    return `<button type="button" class="machine-structure-delete" data-structure-delete="${Number(item.id)}" data-structure-delete-label="${esc(label)}" title="Supprimer">×</button>`;
  }

  function componentCard(item) {
    return `
      <article class="machine-component-card">
        <div class="machine-component-card-head">
          <div>
            <strong>${esc(item.nom)}</strong>
            <span class="machine-component-code">${esc(item.code || "Composant")}</span>
          </div>
          <div class="machine-structure-actions">${deleteButton(item, "ce composant")}</div>
        </div>
        <div class="machine-component-purchase">
          <div class="machine-purchase-cell"><span>Fournisseur</span><strong>${esc(item.supplier_nom || "—")}</strong></div>
          <div class="machine-purchase-cell"><span>Délai</span><strong>${item.delai_obtention_jours == null ? "—" : `${Number(item.delai_obtention_jours)} j`}</strong></div>
          <div class="machine-purchase-cell"><span>Prix</span><strong>${money(item.prix_unitaire)}</strong></div>
        </div>
        ${canEdit ? `
        <details class="machine-component-edit">
          <summary>Modifier fournisseur, délai ou prix</summary>
          <form class="machine-component-edit-form" data-structure-edit="${Number(item.id)}">
            <label>Composant<input name="nom" value="${esc(item.nom)}" required></label>
            <label>Code<input name="code" value="${esc(item.code || "")}" placeholder="Repère"></label>
            <label>Fournisseur<select name="supplier_id">${supplierOptions(item.supplier_id)}</select></label>
            <label>Délai (jours)<input name="delai_obtention_jours" type="number" min="0" step="1" value="${item.delai_obtention_jours == null ? "" : Number(item.delai_obtention_jours)}"></label>
            <label>Prix (€)<input name="prix_unitaire" type="number" min="0" step="0.01" value="${item.prix_unitaire == null ? "" : esc(item.prix_unitaire)}"></label>
            <button type="submit">Enregistrer</button>
          </form>
        </details>` : ""}
      </article>`;
  }

  function renderStructure() {
    const count = root.querySelector("[data-structure-v3-count]");
    if (count) count.textContent = `${state.elements.length} élément${state.elements.length > 1 ? "s" : ""}`;

    if (!state.schema_ready) {
      target.innerHTML = `<div class="machine-structure-warning"><strong>Migration requise.</strong> Applique la migration <code>${esc(state.migration_required || "e14f6a7c2b90")}</code> pour activer fournisseur, délai et prix.</div>`;
      root.querySelectorAll("[data-structure-create] input, [data-structure-create] select, [data-structure-create] button").forEach((el) => { el.disabled = true; });
      return;
    }

    const items = state.elements || [];
    if (!items.length) {
      target.innerHTML = '<div class="machine-structure-empty">Aucune structure technique. Commence par créer un ensemble.</div>';
      updateBuilderSelects();
      return;
    }

    const byId = byIdMap(items);
    const roots = items.filter((item) => depthOf(item, byId) === 0);
    const levelOne = items.filter((item) => depthOf(item, byId) === 1);
    const levelTwo = items.filter((item) => depthOf(item, byId) === 2);
    const legacy = items.filter((item) => depthOf(item, byId) > 2);

    const html = roots.map((ensemble) => {
      const subs = levelOne.filter((item) => Number(item.parent_id) === Number(ensemble.id));
      return `
        <section class="machine-ensemble">
          <div class="machine-ensemble-head">
            <div class="machine-ensemble-title"><span class="machine-level-tag">Ensemble</span><strong>${esc(ensemble.nom)}</strong>${ensemble.code ? `<span class="machine-muted">${esc(ensemble.code)}</span>` : ""}</div>
            <div class="machine-structure-actions">${deleteButton(ensemble, "cet ensemble")}</div>
          </div>
          <div class="machine-subassemblies">
            ${subs.length ? subs.map((sub) => {
              const components = levelTwo.filter((item) => Number(item.parent_id) === Number(sub.id));
              return `
                <section class="machine-subassembly">
                  <div class="machine-subassembly-head">
                    <div class="machine-subassembly-title"><span class="machine-level-tag">Sous-ensemble</span><strong>${esc(sub.nom)}</strong>${sub.code ? `<span class="machine-muted">${esc(sub.code)}</span>` : ""}</div>
                    <div class="machine-structure-actions">${deleteButton(sub, "ce sous-ensemble")}</div>
                  </div>
                  <div class="machine-component-list">
                    ${components.length ? components.map(componentCard).join("") : '<div class="machine-structure-empty">Aucun composant dans ce sous-ensemble.</div>'}
                  </div>
                </section>`;
            }).join("") : '<div class="machine-structure-empty">Aucun sous-ensemble dans cet ensemble.</div>'}
          </div>
        </section>`;
    }).join("");

    target.innerHTML = `<div class="machine-structure-tree">${html || '<div class="machine-structure-empty">Aucun ensemble racine exploitable.</div>'}</div>` +
      (legacy.length ? `<div class="machine-legacy-structure"><h3>Éléments d’une ancienne arborescence</h3><p class="machine-muted">Ces éléments dépassent les trois niveaux actuels et restent conservés.</p>${legacy.map((item) => `<div class="machine-legacy-item">${esc(item.nom)} · niveau historique ${depthOf(item, byId) + 1}</div>`).join("")}</div>` : "");

    updateBuilderSelects();
  }

  function updateBuilderSelects() {
    const items = state.elements || [];
    const byId = byIdMap(items);
    const ensembles = items.filter((item) => depthOf(item, byId) === 0);
    const subs = items.filter((item) => depthOf(item, byId) === 1);

    const ensembleSelect = root.querySelector('[data-structure-create="sous_ensemble"] select[name="parent_id"]');
    if (ensembleSelect) {
      ensembleSelect.innerHTML = '<option value="">Choisir un ensemble *</option>' + ensembles.map((item) => `<option value="${Number(item.id)}">${esc(item.nom)}</option>`).join("");
    }

    const subSelect = root.querySelector('[data-structure-create="composant"] select[name="parent_id"]');
    if (subSelect) {
      subSelect.innerHTML = '<option value="">Choisir un sous-ensemble *</option>' + subs.map((item) => {
        const parent = byId.get(Number(item.parent_id));
        return `<option value="${Number(item.id)}">${esc(parent?.nom || "Ensemble")} → ${esc(item.nom)}</option>`;
      }).join("");
    }

    const supplierSelect = root.querySelector('[data-structure-create="composant"] select[name="supplier_id"]');
    if (supplierSelect) supplierSelect.innerHTML = supplierOptions();
  }

  function formDataObject(form) {
    return Object.fromEntries(new FormData(form).entries());
  }

  async function loadStructure() {
    state = await jsonRequest(`/api/equipements/${encodeURIComponent(equipmentId)}/structure-technique-v3`, { method: "GET" });
    renderStructure();
  }

  root.querySelectorAll("[data-structure-create]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const payload = formDataObject(form);
      payload.niveau = form.dataset.structureCreate;
      try {
        await jsonRequest(`/api/equipements/${equipmentId}/structure-technique-v3`, { method: "POST", body: JSON.stringify(payload) });
        form.reset();
        await loadStructure();
      } catch (error) {
        window.alert(error.message);
      }
    });
  });

  root.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-structure-edit]");
    if (!form) return;
    event.preventDefault();
    try {
      await jsonRequest(`/api/equipements/${equipmentId}/structure-technique-v3/${form.dataset.structureEdit}`, {
        method: "PATCH",
        body: JSON.stringify(formDataObject(form)),
      });
      await loadStructure();
    } catch (error) {
      window.alert(error.message);
    }
  });

  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-structure-delete]");
    if (!button) return;
    const label = button.dataset.structureDeleteLabel || "cet élément";
    if (!window.confirm(`Supprimer ${label} ? Les éléments contenant des enfants doivent être vidés d'abord.`)) return;
    try {
      await jsonRequest(`/api/equipements/${equipmentId}/structure-technique-v3/${button.dataset.structureDelete}`, { method: "DELETE" });
      await loadStructure();
    } catch (error) {
      window.alert(error.message);
    }
  });

  loadStructure().catch((error) => {
    target.innerHTML = `<div class="machine-api-error">Impossible de charger l'arborescence technique : ${esc(error.message)}</div>`;
  });
});
