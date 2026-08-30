(() => {
  const root = document.querySelector("[data-machine-dossier]");
  const legacyForm = root?.querySelector("[data-compatible-part-form]");
  if (!root || !legacyForm) return;

  const equipmentId = root.dataset.equipmentId;
  const canEdit = root.dataset.canEdit === "1";
  if (!canEdit) return;

  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[char]));

  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, {
      headers: {
        "Accept": "application/json",
        "Content-Type": "application/json",
        ...(options.headers || {}),
      },
      ...options,
    });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw new Error(data.error || `Erreur ${response.status}`);
    return data;
  }

  const form = document.createElement("form");
  form.className = legacyForm.className;
  form.setAttribute("data-machine-part-link-form", "");
  form.innerHTML = `
    <select name="article_id" data-part-article required>
      <option value="">Chargement des articles du stock…</option>
    </select>
    <select name="component_id" data-part-component required>
      <option value="">Chargement des composants…</option>
    </select>
    <input name="quantite_recommandee" type="number" min="0" step="0.001" placeholder="Qté recommandée">
    <label class="machine-check"><input type="checkbox" name="critique"> Pièce critique</label>
    <input name="notes" placeholder="Notes">
    <button class="btn-primary" type="submit">Lier la pièce au composant</button>
  `;
  legacyForm.replaceWith(form);

  const articleSelect = form.querySelector("[data-part-article]");
  const componentSelect = form.querySelector("[data-part-component]");
  const submitButton = form.querySelector('button[type="submit"]');

  async function loadOptions() {
    const currentArticle = articleSelect.value;
    const currentComponent = componentSelect.value;
    const data = await jsonRequest(`/api/equipements/${encodeURIComponent(equipmentId)}/part-link-options`, { method: "GET" });

    const articles = data.articles || [];
    const components = data.components || [];

    articleSelect.innerHTML = '<option value="">Choisir une pièce du stock *</option>' + articles.map((article) => {
      const manufacturer = article.fabricant ? ` · ${esc(article.fabricant)}` : "";
      return `<option value="${Number(article.id)}">${esc(article.reference)} — ${esc(article.designation || "Sans désignation")}${manufacturer}</option>`;
    }).join("");

    componentSelect.innerHTML = '<option value="">Choisir le composant cible *</option>' + components.map((component) =>
      `<option value="${Number(component.id)}">${esc(component.ensemble_nom)} → ${esc(component.sous_ensemble_nom)} → ${esc(component.nom)}</option>`
    ).join("");

    if ([...articleSelect.options].some((option) => option.value === currentArticle)) articleSelect.value = currentArticle;
    if ([...componentSelect.options].some((option) => option.value === currentComponent)) componentSelect.value = currentComponent;

    submitButton.disabled = !articles.length || !components.length;
    if (!articles.length) articleSelect.innerHTML = '<option value="">Aucun article actif dans le stock</option>';
    if (!components.length) componentSelect.innerHTML = '<option value="">Crée d’abord un composant dans l’arborescence</option>';
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const payload = {
      article_id: data.get("article_id"),
      component_id: data.get("component_id"),
      quantite_recommandee: data.get("quantite_recommandee"),
      critique: form.querySelector('input[name="critique"]').checked,
      notes: data.get("notes"),
    };

    submitButton.disabled = true;
    try {
      await jsonRequest(`/api/equipements/${encodeURIComponent(equipmentId)}/part-links`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      sessionStorage.setItem(`machine-active-tab-${equipmentId}`, "parts");
      window.location.reload();
    } catch (error) {
      window.alert(error.message);
      submitButton.disabled = false;
    }
  });

  root.addEventListener("machine-structure-updated", () => {
    loadOptions().catch((error) => window.console.error("Actualisation liaison stock/composant", error));
  });

  const wantedTab = sessionStorage.getItem(`machine-active-tab-${equipmentId}`);
  if (wantedTab) {
    sessionStorage.removeItem(`machine-active-tab-${equipmentId}`);
    window.addEventListener("DOMContentLoaded", () => {
      root.querySelector(`[data-machine-tab="${wantedTab}"]`)?.click();
    }, { once: true });
  }

  loadOptions().catch((error) => {
    articleSelect.innerHTML = `<option value="">${esc(error.message)}</option>`;
    componentSelect.innerHTML = '<option value="">Indisponible</option>';
    submitButton.disabled = true;
  });
})();
