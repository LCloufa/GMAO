document.addEventListener("DOMContentLoaded", () => {
  const root = document.querySelector("[data-machine-dossier]");
  if (!root) return;

  const equipmentId = root.dataset.equipmentId;
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

  const money = (value) => new Intl.NumberFormat("fr-FR", {
    style: "currency", currency: "EUR", maximumFractionDigits: 2
  }).format(Number(value || 0));

  const statusLabel = {
    planned: "Planifiée", in_progress: "En cours", completed: "Terminée",
    cancelled: "Annulée", postponed: "Reportée", pending: "En attente",
    resolved: "Résolue", rejected: "Rejetée"
  };

  const badgeClass = (status) => {
    if (["completed", "resolved"].includes(status)) return "success";
    if (["critical", "in_progress", "pending"].includes(status)) return "warning";
    if (["cancelled", "rejected"].includes(status)) return "danger";
    return "";
  };

  root.querySelectorAll("[data-machine-tab]").forEach((button) => {
    button.addEventListener("click", () => {
      const target = button.dataset.machineTab;
      root.querySelectorAll("[data-machine-tab]").forEach((item) => item.classList.toggle("active", item === button));
      root.querySelectorAll("[data-machine-panel]").forEach((panel) => panel.classList.toggle("active", panel.dataset.machinePanel === target));
    });
  });

  const setText = (selector, value) => {
    const el = root.querySelector(selector);
    if (el) el.textContent = value;
  };

  const renderInterventions = (items) => {
    const target = root.querySelector("[data-machine-interventions]");
    if (!target) return;
    if (!items.length) {
      target.innerHTML = '<div class="machine-empty">Aucune intervention enregistrée pour cette machine.</div>';
      return;
    }
    target.innerHTML = `
      <div class="machine-table-wrap"><table class="machine-table">
        <thead><tr><th>Intervention</th><th>Date</th><th>Technicien</th><th>Priorité</th><th>Statut</th><th>Rapport</th></tr></thead>
        <tbody>${items.map((item) => `
          <tr>
            <td><strong>${esc(item.title || "Intervention")}</strong><br><span class="machine-muted">${esc(item.type || "")}</span></td>
            <td>${dateOnlyFr(item.scheduled_date)} ${esc(item.scheduled_time || "")}</td>
            <td>${esc(item.technicien_code || "—")}</td>
            <td><span class="machine-badge ${badgeClass(item.priority)}">${esc(item.priority || "—")}</span></td>
            <td><span class="machine-badge ${badgeClass(item.status)}">${esc(statusLabel[item.status] || item.status || "—")}</span></td>
            <td>${item.rapport_id ? `<a href="/rapports/${Number(item.rapport_id)}/pdf" target="_blank">PDF</a>` : "—"}</td>
          </tr>`).join("")}</tbody>
      </table></div>`;
  };

  const renderFailures = (items) => {
    const target = root.querySelector("[data-machine-failures]");
    if (!target) return;
    if (!items.length) {
      target.innerHTML = '<div class="machine-empty">Aucune panne déclarée pour cette machine.</div>';
      return;
    }
    target.innerHTML = `
      <div class="machine-table-wrap"><table class="machine-table">
        <thead><tr><th>Panne</th><th>Déclarée le</th><th>Urgence</th><th>Déclarant</th><th>Statut</th></tr></thead>
        <tbody>${items.map((item) => `
          <tr>
            <td><strong>${esc(item.title || "Panne")}</strong><br><span class="machine-muted">${esc(item.description || "")}</span></td>
            <td>${dateFr(item.created_at)}</td>
            <td><span class="machine-badge ${badgeClass(item.urgency)}">${esc(item.urgency || "—")}</span></td>
            <td>${esc(item.declarant || "—")}</td>
            <td><span class="machine-badge ${badgeClass(item.status)}">${esc(statusLabel[item.status] || item.status || "—")}</span></td>
          </tr>`).join("")}</tbody>
      </table></div>`;
  };

  const renderParts = (items) => {
    const target = root.querySelector("[data-machine-parts]");
    if (!target) return;
    if (!items.length) {
      target.innerHTML = '<div class="machine-empty">Aucune pièce consommée ou retournée sur cette machine.</div>';
      return;
    }
    target.innerHTML = `
      <div class="machine-table-wrap"><table class="machine-table">
        <thead><tr><th>Article</th><th>Intervention</th><th>Quantité</th><th>Prix unitaire</th><th>Date</th></tr></thead>
        <tbody>${items.map((item) => `
          <tr>
            <td><strong>${esc(item.reference || "—")}</strong><br><span class="machine-muted">${esc(item.designation || "")}</span></td>
            <td>${esc(item.intervention_title || "—")}</td>
            <td>${esc(item.quantite_utilisee ?? 0)}</td>
            <td>${money(item.prix_unitaire)}</td>
            <td>${dateFr(item.created_at)}</td>
          </tr>`).join("")}</tbody>
      </table></div>`;
  };

  const renderTimeline = (items) => {
    const target = root.querySelector("[data-machine-timeline]");
    if (!target) return;
    if (!items.length) {
      target.innerHTML = '<div class="machine-empty">L’historique se construira automatiquement avec l’activité de la machine.</div>';
      return;
    }
    target.innerHTML = `<div class="machine-timeline">${items.map((item) => `
      <div class="machine-event ${esc(item.severity || "info")}">
        <div class="machine-event-time">${dateFr(item.when)}</div>
        <div class="machine-event-title">${esc(item.title || "Événement")}</div>
        ${item.detail ? `<div class="machine-event-detail">${esc(item.detail)}</div>` : ""}
      </div>`).join("")}</div>`;
  };

  const renderHighlights = (data) => {
    const failure = root.querySelector("[data-machine-last-failure]");
    if (failure) {
      failure.innerHTML = data.last_failure
        ? `<strong>${esc(data.last_failure.title || "Panne")}</strong><span class="machine-muted">${dateFr(data.last_failure.created_at)} · ${esc(statusLabel[data.last_failure.status] || data.last_failure.status || "")}</span>`
        : '<strong>Aucune panne enregistrée</strong><span class="machine-muted">Aucun incident connu dans le dossier.</span>';
    }
    const next = root.querySelector("[data-machine-next-maintenance]");
    if (next) {
      next.innerHTML = data.next_maintenance
        ? `<strong>${esc(data.next_maintenance.title || "Maintenance")}</strong><span class="machine-muted">${dateOnlyFr(data.next_maintenance.scheduled_date)} ${esc(data.next_maintenance.scheduled_time || "")}</span>`
        : '<strong>Aucune maintenance planifiée</strong><span class="machine-muted">Aucune intervention future enregistrée.</span>';
    }
  };

  fetch(`/api/equipements/${encodeURIComponent(equipmentId)}/dossier`, { headers: { "Accept": "application/json" } })
    .then(async (response) => {
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `Erreur ${response.status}`);
      return data;
    })
    .then((data) => {
      root.querySelectorAll("[data-machine-loading]").forEach((el) => el.remove());
      setText("[data-kpi-availability]", `${Number(data.kpis.availability_rate || 0).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %`);
      setText("[data-kpi-downtime]", `${Number(data.kpis.downtime_hours_30d || 0).toLocaleString("fr-FR")} h`);
      setText("[data-kpi-interventions]", data.kpis.total_interventions ?? 0);
      setText("[data-kpi-open]", data.kpis.open_interventions ?? 0);
      setText("[data-kpi-failures]", data.kpis.open_failures ?? 0);
      setText("[data-kpi-cost]", money(data.kpis.parts_cost));
      setText("[data-report-count]", data.kpis.reports_count ?? 0);
      renderHighlights(data);
      renderInterventions(data.interventions || []);
      renderFailures(data.declarations || []);
      renderParts(data.parts || []);
      renderTimeline(data.timeline || []);
    })
    .catch((error) => {
      root.querySelectorAll("[data-machine-loading]").forEach((el) => {
        el.className = "machine-api-error";
        el.textContent = `Impossible de charger les données du dossier : ${error.message}`;
      });
    });
});