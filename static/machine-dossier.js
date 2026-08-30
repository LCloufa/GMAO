document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-machine-dossier]");
  if (!root) return;

  const equipmentId = root.dataset.equipmentId;
  const canEdit = root.dataset.canEdit === "1";
  let dossier = null;

  const esc = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[char]));

  const dateFr = (value) => {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return esc(value);
    return new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium", timeStyle: "short" }).format(d);
  };
  const dateOnlyFr = (value) => {
    if (!value) return "—";
    const d = new Date(`${String(value).slice(0, 10)}T12:00:00`);
    if (Number.isNaN(d.getTime())) return esc(value);
    return new Intl.DateTimeFormat("fr-FR", { dateStyle: "medium" }).format(d);
  };
  const money = (value) => new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 2 }).format(Number(value || 0));
  const statusLabel = { planned: "Planifiée", in_progress: "En cours", completed: "Terminée", cancelled: "Annulée", postponed: "Reportée", pending: "En attente", resolved: "Résolue", rejected: "Rejetée" };
  const badgeClass = (status) => (["completed", "resolved"].includes(status) ? "success" : ["critical", "high", "in_progress", "pending"].includes(status) ? "warning" : ["cancelled", "rejected"].includes(status) ? "danger" : "");
  const setText = (selector, value) => { const el = root.querySelector(selector); if (el) el.textContent = value; };

  root.querySelectorAll("[data-machine-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.machineTab;
      root.querySelectorAll("[data-machine-tab]").forEach((item) => item.classList.toggle("active", item === button));
      root.querySelectorAll("[data-machine-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.machinePanel === target));
    });
  });

  async function jsonRequest(url, options = {}) {
    const response = await fetch(url, { headers: { "Accept": "application/json", "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
    let data = {};
    try { data = await response.json(); } catch (_) { data = {}; }
    if (!response.ok) throw new Error(data.error || `Erreur ${response.status}`);
    return data;
  }

  function componentDepth(item, byId) {
    let depth = 0;
    let current = item;
    const seen = new Set();
    while (current?.parent_id && byId.has(Number(current.parent_id)) && !seen.has(Number(current.parent_id))) {
      seen.add(Number(current.parent_id));
      current = byId.get(Number(current.parent_id));
      depth += 1;
    }
    return depth;
  }

  function updateComponentSelects(items) {
    const byId = new Map(items.map((item) => [Number(item.id), item]));
    const sorted = [...items].sort((a, b) => componentDepth(a, byId) - componentDepth(b, byId) || String(a.nom).localeCompare(String(b.nom), "fr"));
    root.querySelectorAll("[data-component-select], [data-component-parent]").forEach((select) => {
      const first = select.hasAttribute("data-component-parent") ? "Racine de la machine" : "Machine entière";
      select.innerHTML = `<option value="">${first}</option>` + sorted.map((item) => {
        const depth = componentDepth(item, byId);
        return `<option value="${Number(item.id)}">${"— ".repeat(depth)}${esc(item.nom)}</option>`;
      }).join("");
    });
  }

  function renderComponents(items) {
    setText("[data-component-count]", `${items.length} élément${items.length > 1 ? "s" : ""}`);
    updateComponentSelects(items);
    const target = root.querySelector("[data-machine-components]");
    if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">Aucun sous-ensemble ou composant.</div>'; return; }

    const children = new Map();
    items.forEach((item) => {
      const key = item.parent_id ? Number(item.parent_id) : 0;
      if (!children.has(key)) children.set(key, []);
      children.get(key).push(item);
    });
    const renderLevel = (parentId, depth = 0) => (children.get(parentId) || []).map((item) => `
      <div class="machine-tree-item" style="--tree-depth:${depth}">
        <div class="machine-tree-main">
          <span class="machine-tree-icon">${item.type_composant === "Sous-ensemble" ? "▣" : "◇"}</span>
          <div><strong>${esc(item.nom)}</strong><span>${esc(item.code || item.type_composant || "Composant")}${item.fabricant ? ` · ${esc(item.fabricant)}` : ""}</span></div>
        </div>
        <div class="machine-tree-actions"><span class="machine-badge ${badgeClass(item.criticite)}">${esc(item.criticite || "medium")}</span>${canEdit ? `<button type="button" class="machine-icon-btn danger" data-delete-component="${Number(item.id)}" title="Supprimer">×</button>` : ""}</div>
      </div>
      ${renderLevel(Number(item.id), depth + 1)}
    `).join("");
    target.innerHTML = `<div class="machine-tree">${renderLevel(0)}</div>`;
  }

  function renderSpecifications(items) {
    setText("[data-spec-count]", `${items.length}`);
    const target = root.querySelector("[data-machine-specifications]");
    if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">Aucune caractéristique personnalisée.</div>'; return; }
    const groups = new Map();
    items.forEach((item) => { const group = item.groupe || "Général"; if (!groups.has(group)) groups.set(group, []); groups.get(group).push(item); });
    target.innerHTML = [...groups.entries()].map(([group, rows]) => `
      <div class="machine-spec-group"><h3>${esc(group)}</h3><div class="machine-data-list">${rows.map((item) => `
        <div class="machine-data-row machine-spec-row"><span>${esc(item.nom)}${item.component_nom ? `<small>${esc(item.component_nom)}</small>` : ""}</span><span>${esc(item.valeur || "—")} ${esc(item.unite || "")}${canEdit ? `<button type="button" class="machine-icon-btn danger" data-delete-spec="${Number(item.id)}">×</button>` : ""}</span></div>
      `).join("")}</div></div>
    `).join("");
  }

  function renderCounters(items) {
    setText("[data-counter-count]", `${items.length}`);
    const target = root.querySelector("[data-machine-counters]");
    if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">Aucun compteur configuré.</div>'; return; }
    target.innerHTML = `<div class="machine-counter-grid">${items.map((item) => `
      <article class="machine-counter-card">
        <div class="machine-counter-head"><div><span>${esc(item.component_nom || "Machine entière")}</span><strong>${esc(item.nom)}</strong></div>${canEdit ? `<button type="button" class="machine-icon-btn danger" data-delete-counter="${Number(item.id)}">×</button>` : ""}</div>
        <div class="machine-counter-value">${item.valeur_actuelle == null ? "—" : Number(item.valeur_actuelle).toLocaleString("fr-FR", { maximumFractionDigits: 3 })} <small>${esc(item.unite || "")}</small></div>
        <div class="machine-muted">Dernier relevé : ${dateFr(item.dernier_releve_at)}</div>
        ${canEdit ? `<form class="machine-reading-form" data-reading-form="${Number(item.id)}"><input name="valeur" type="number" step="0.001" placeholder="Nouveau relevé" required><input name="note" placeholder="Note"><button type="submit">Enregistrer</button></form>` : ""}
      </article>`).join("")}</div>`;
  }

  function renderCompatibleParts(items) {
    setText("[data-compatible-part-count]", `${items.length}`);
    const target = root.querySelector("[data-machine-compatible-parts]");
    if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">Aucune pièce compatible enregistrée.</div>'; return; }
    target.innerHTML = `<div class="machine-table-wrap"><table class="machine-table"><thead><tr><th>Référence</th><th>Désignation</th><th>Niveau</th><th>Qté conseillée</th><th>Criticité</th><th></th></tr></thead><tbody>${items.map((item) => `
      <tr><td><strong>${esc(item.reference)}</strong></td><td>${esc(item.designation || "—")}</td><td>${esc(item.component_nom || "Machine entière")}</td><td>${item.quantite_recommandee == null ? "—" : esc(item.quantite_recommandee)}</td><td>${item.critique ? '<span class="machine-badge danger">Critique</span>' : '<span class="machine-badge">Standard</span>'}</td><td>${canEdit ? `<button type="button" class="machine-icon-btn danger" data-delete-compatible-part="${Number(item.id)}">×</button>` : ""}</td></tr>
    `).join("")}</tbody></table></div>`;
  }

  function renderInterventions(items) {
    const target = root.querySelector("[data-machine-interventions]"); if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">Aucune intervention enregistrée.</div>'; return; }
    target.innerHTML = `<div class="machine-table-wrap"><table class="machine-table"><thead><tr><th>Intervention</th><th>Date</th><th>Technicien</th><th>Priorité</th><th>Statut</th><th>Rapport</th></tr></thead><tbody>${items.map((item) => `<tr><td><strong>${esc(item.title || "Intervention")}</strong><br><span class="machine-muted">${esc(item.type || "")}</span></td><td>${dateOnlyFr(item.scheduled_date)} ${esc(item.scheduled_time || "")}</td><td>${esc(item.technicien_code || "—")}</td><td><span class="machine-badge ${badgeClass(item.priority)}">${esc(item.priority || "—")}</span></td><td><span class="machine-badge ${badgeClass(item.status)}">${esc(statusLabel[item.status] || item.status || "—")}</span></td><td>${item.rapport_id ? `<a href="/rapports/${Number(item.rapport_id)}/pdf" target="_blank">PDF</a>` : "—"}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderFailures(items) {
    const target = root.querySelector("[data-machine-failures]"); if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">Aucune panne déclarée.</div>'; return; }
    target.innerHTML = `<div class="machine-table-wrap"><table class="machine-table"><thead><tr><th>Panne</th><th>Déclarée le</th><th>Urgence</th><th>Déclarant</th><th>Statut</th></tr></thead><tbody>${items.map((item) => `<tr><td><strong>${esc(item.title || "Panne")}</strong><br><span class="machine-muted">${esc(item.description || "")}</span></td><td>${dateFr(item.created_at)}</td><td><span class="machine-badge ${badgeClass(item.urgency)}">${esc(item.urgency || "—")}</span></td><td>${esc(item.declarant || "—")}</td><td><span class="machine-badge ${badgeClass(item.status)}">${esc(statusLabel[item.status] || item.status || "—")}</span></td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderParts(items) {
    const target = root.querySelector("[data-machine-parts]"); if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">Aucune consommation enregistrée.</div>'; return; }
    target.innerHTML = `<div class="machine-table-wrap"><table class="machine-table"><thead><tr><th>Article</th><th>Intervention</th><th>Quantité</th><th>Prix unitaire</th><th>Date</th></tr></thead><tbody>${items.map((item) => `<tr><td><strong>${esc(item.reference || "—")}</strong><br><span class="machine-muted">${esc(item.designation || "")}</span></td><td>${esc(item.intervention_title || "—")}</td><td>${esc(item.quantite_utilisee ?? 0)}</td><td>${money(item.prix_unitaire)}</td><td>${dateFr(item.created_at)}</td></tr>`).join("")}</tbody></table></div>`;
  }

  function renderTimeline(items) {
    const target = root.querySelector("[data-machine-timeline]"); if (!target) return;
    if (!items.length) { target.innerHTML = '<div class="machine-empty">L’historique se construira automatiquement avec l’activité.</div>'; return; }
    target.innerHTML = `<div class="machine-timeline">${items.map((item) => `<div class="machine-event ${esc(item.severity || "info")}"><div class="machine-event-time">${dateFr(item.when)}</div><div class="machine-event-title">${esc(item.title || "Événement")}</div>${item.detail ? `<div class="machine-event-detail">${esc(item.detail)}</div>` : ""}</div>`).join("")}</div>`;
  }

  function renderHighlights(data) {
    const failure = root.querySelector("[data-machine-last-failure]");
    if (failure) failure.innerHTML = data.last_failure ? `<strong>${esc(data.last_failure.title || "Panne")}</strong><span class="machine-muted">${dateFr(data.last_failure.created_at)} · ${esc(statusLabel[data.last_failure.status] || data.last_failure.status || "")}</span>` : '<strong>Aucune panne enregistrée</strong><span class="machine-muted">Aucun incident connu.</span>';
    const next = root.querySelector("[data-machine-next-maintenance]");
    if (next) next.innerHTML = data.next_maintenance ? `<strong>${esc(data.next_maintenance.title || "Maintenance")}</strong><span class="machine-muted">${dateOnlyFr(data.next_maintenance.scheduled_date)} ${esc(data.next_maintenance.scheduled_time || "")}</span>` : '<strong>Aucune maintenance planifiée</strong><span class="machine-muted">Aucune intervention future enregistrée.</span>';
  }

  function renderAll(data) {
    dossier = data;
    root.querySelectorAll("[data-machine-loading]").forEach((el) => el.remove());
    const warning = root.querySelector("[data-machine-schema-warning]"); if (warning) warning.hidden = Boolean(data.schema_ready);
    setText("[data-kpi-availability]", `${Number(data.kpis.availability_rate || 0).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %`);
    setText("[data-kpi-downtime]", `${Number(data.kpis.downtime_hours_30d || 0).toLocaleString("fr-FR")} h`);
    setText("[data-kpi-interventions]", data.kpis.total_interventions ?? 0); setText("[data-kpi-open]", data.kpis.open_interventions ?? 0); setText("[data-kpi-failures]", data.kpis.open_failures ?? 0); setText("[data-kpi-cost]", money(data.kpis.parts_cost)); setText("[data-report-count]", data.kpis.reports_count ?? 0);
    renderHighlights(data); renderComponents(data.components || []); renderSpecifications(data.specifications || []); renderCounters(data.counters || []); renderCompatibleParts(data.compatible_parts || []); renderInterventions(data.interventions || []); renderFailures(data.declarations || []); renderParts(data.parts || []); renderTimeline(data.timeline || []);
    root.querySelectorAll("form button, form input, form select, form textarea").forEach((el) => { if (!data.schema_ready && el.closest("[data-component-form], [data-spec-form], [data-counter-form], [data-compatible-part-form]")) el.disabled = true; });
  }

  async function loadDossier() {
    const data = await jsonRequest(`/api/equipements/${encodeURIComponent(equipmentId)}/dossier`, { method: "GET", headers: { "Content-Type": "application/json" } });
    renderAll(data);
  }

  function formPayload(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    form.querySelectorAll('input[type="checkbox"]').forEach((checkbox) => { data[checkbox.name] = checkbox.checked; });
    return data;
  }

  async function submitForm(form, url) {
    try { await jsonRequest(url, { method: "POST", body: JSON.stringify(formPayload(form)) }); form.reset(); await loadDossier(); }
    catch (error) { window.alert(error.message); }
  }

  root.querySelector("[data-component-form]")?.addEventListener("submit", (event) => { event.preventDefault(); submitForm(event.currentTarget, `/api/equipements/${equipmentId}/components`); });
  root.querySelector("[data-spec-form]")?.addEventListener("submit", (event) => { event.preventDefault(); submitForm(event.currentTarget, `/api/equipements/${equipmentId}/specifications`); });
  root.querySelector("[data-counter-form]")?.addEventListener("submit", (event) => { event.preventDefault(); submitForm(event.currentTarget, `/api/equipements/${equipmentId}/counters`); });
  root.querySelector("[data-compatible-part-form]")?.addEventListener("submit", (event) => { event.preventDefault(); submitForm(event.currentTarget, `/api/equipements/${equipmentId}/compatible-parts`); });

  root.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-reading-form]"); if (!form) return;
    event.preventDefault();
    const counterId = form.dataset.readingForm;
    await submitForm(form, `/api/equipements/${equipmentId}/counters/${counterId}/readings`);
  });

  root.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-delete-component], [data-delete-spec], [data-delete-counter], [data-delete-compatible-part]");
    if (!button) return;
    if (!window.confirm("Confirmer la suppression de cet élément du dossier machine ?")) return;
    let url = "";
    if (button.dataset.deleteComponent) url = `/api/equipements/${equipmentId}/components/${button.dataset.deleteComponent}`;
    if (button.dataset.deleteSpec) url = `/api/equipements/${equipmentId}/specifications/${button.dataset.deleteSpec}`;
    if (button.dataset.deleteCounter) url = `/api/equipements/${equipmentId}/counters/${button.dataset.deleteCounter}`;
    if (button.dataset.deleteCompatiblePart) url = `/api/equipements/${equipmentId}/compatible-parts/${button.dataset.deleteCompatiblePart}`;
    try { await jsonRequest(url, { method: "DELETE" }); await loadDossier(); } catch (error) { window.alert(error.message); }
  });

  loadDossier().catch((error) => {
    root.querySelectorAll("[data-machine-loading]").forEach((el) => { el.className = "machine-api-error"; el.textContent = `Impossible de charger les données du dossier : ${error.message}`; });
  });
});
