(function () {
    "use strict";

    if (!window.FullCalendar || !window.FullCalendar.Calendar) {
        return;
    }

    const Calendar = window.FullCalendar.Calendar;
    const originalRender = Calendar.prototype.render;

    function parseInterventionStart(details) {
        const date = details.scheduled_date;
        const time = details.scheduled_time || "08:00";
        if (!date) return null;

        const parsed = new Date(`${date}T${time}`);
        return Number.isNaN(parsed.getTime()) ? null : parsed;
    }

    function atTime(day, hour, minute) {
        const value = new Date(day);
        value.setHours(hour, minute, 0, 0);
        return value;
    }

    function nextWorkingMorning(value) {
        const next = new Date(value);
        next.setDate(next.getDate() + 1);
        next.setHours(8, 0, 0, 0);

        while (next.getDay() === 0 || next.getDay() === 6) {
            next.setDate(next.getDate() + 1);
        }

        return next;
    }

    function buildWorkingSegments(start, end, critical) {
        if (!(start instanceof Date) || !(end instanceof Date) || end <= start) {
            return [];
        }

        // Une intervention critique peut continuer 24 h/24, week-end compris.
        if (critical) {
            return [{ start: new Date(start), end: new Date(end) }];
        }

        const segments = [];
        let cursor = new Date(start);
        let safety = 0;

        while (cursor < end && safety < 5000) {
            safety += 1;

            const day = cursor.getDay();
            if (day === 0 || day === 6) {
                cursor = nextWorkingMorning(cursor);
                continue;
            }

            const morningStart = atTime(cursor, 8, 0);
            const morningEnd = atTime(cursor, 12, 0);
            const afternoonStart = atTime(cursor, 13, 0);
            const afternoonEnd = atTime(cursor, 17, 0);
            const slots = [
                [morningStart, morningEnd],
                [afternoonStart, afternoonEnd]
            ];

            for (const [slotStart, slotEnd] of slots) {
                const segmentStart = new Date(Math.max(cursor.getTime(), slotStart.getTime()));
                const segmentEnd = new Date(Math.min(end.getTime(), slotEnd.getTime()));

                if (segmentStart < segmentEnd) {
                    segments.push({ start: segmentStart, end: segmentEnd });
                }
            }

            cursor = nextWorkingMorning(cursor);
        }

        return segments;
    }

    function snapshotEvent(event) {
        return {
            title: event.title,
            backgroundColor: event.backgroundColor,
            borderColor: event.borderColor,
            textColor: event.textColor,
            extendedProps: { ...(event.extendedProps || {}) }
        };
    }

    function cloneEventData(source, segment, index) {
        const props = source.extendedProps || {};
        const originalId = props.orig_id;

        return {
            id: `open-${originalId}-${index}`,
            title: source.title,
            start: segment.start,
            end: segment.end,
            backgroundColor: source.backgroundColor,
            borderColor: source.borderColor,
            textColor: source.textColor,
            extendedProps: {
                ...props,
                open_duration: true
            }
        };
    }

    async function loadDetails(calendar, interventionId) {
        calendar.__gmaoOpenDurationDetails = calendar.__gmaoOpenDurationDetails || new Map();
        const cached = calendar.__gmaoOpenDurationDetails.get(interventionId);
        const now = Date.now();

        // On rafraîchit régulièrement le statut pour détecter un rapport créé
        // depuis un autre poste sans devoir recharger toute la page.
        if (cached && now - cached.fetchedAt < 45000) {
            return cached.value;
        }

        try {
            const response = await fetch(`/interventions/${interventionId}/details`, {
                credentials: "same-origin",
                cache: "no-store"
            });

            if (!response.ok) {
                return cached ? cached.value : null;
            }

            const details = await response.json();
            calendar.__gmaoOpenDurationDetails.set(interventionId, {
                value: details,
                fetchedAt: now
            });
            return details;
        } catch (error) {
            console.error("Impossible de charger l'intervention à durée ouverte", error);
            return cached ? cached.value : null;
        }
    }

    function removeEventsForIntervention(calendar, interventionId) {
        for (const event of calendar.getEvents()) {
            const props = event.extendedProps || {};
            if (String(props.orig_id) === String(interventionId)) {
                event.remove();
            }
        }
    }

    async function refreshOpenDurationEvents(calendar) {
        if (!calendar || calendar.__gmaoOpenDurationRefreshing) {
            return;
        }

        calendar.__gmaoOpenDurationRefreshing = true;

        try {
            calendar.__gmaoOpenDurationSources = calendar.__gmaoOpenDurationSources || new Map();

            // Mémorise le style et les informations du bloc initial avant de le
            // remplacer par les segments dynamiques. La copie reste disponible
            // aux rafraîchissements suivants, même après suppression du bloc initial.
            for (const event of calendar.getEvents()) {
                const props = event.extendedProps || {};
                const originalId = props.orig_id;

                if (!originalId || props.open_duration) {
                    continue;
                }

                if (!calendar.__gmaoOpenDurationSources.has(originalId)) {
                    calendar.__gmaoOpenDurationSources.set(originalId, snapshotEvent(event));
                }
            }

            const now = new Date();

            for (const [interventionId, source] of calendar.__gmaoOpenDurationSources.entries()) {
                const details = await loadDetails(calendar, interventionId);
                if (!details) continue;

                const duration = Number(details.estimated_duration || 0);
                const status = String(details.status || "").toLowerCase();
                const isActive = ["planned", "in_progress"].includes(status);

                // Une durée positive reste gérée par la segmentation historique.
                if (duration > 0) {
                    continue;
                }

                // Un rapport clôt l'intervention. On ne prolonge plus les segments
                // déjà affichés ; ils restent figés sur la page courante.
                if (!isActive) {
                    continue;
                }

                const start = parseInterventionStart(details);
                if (!start) continue;

                // Avant l'heure de départ, le bloc provisoire généré par le serveur
                // reste visible pour matérialiser la planification.
                if (now <= start) {
                    continue;
                }

                const priority = String(details.priority || source.extendedProps.priority || "").toLowerCase();
                const critical = priority === "critical";
                const segments = buildWorkingSegments(start, now, critical);

                removeEventsForIntervention(calendar, interventionId);

                segments.forEach((segment, index) => {
                    calendar.addEvent(cloneEventData(source, segment, index));
                });
            }
        } finally {
            calendar.__gmaoOpenDurationRefreshing = false;
        }
    }

    Calendar.prototype.render = function () {
        const result = originalRender.apply(this, arguments);

        // Le détail de l'intervention permet de distinguer une vraie durée
        // (ex. 60 min) d'une durée laissée vide, enregistrée à 0 minute.
        setTimeout(() => refreshOpenDurationEvents(this), 0);

        if (!this.__gmaoOpenDurationTimer) {
            this.__gmaoOpenDurationTimer = window.setInterval(() => {
                refreshOpenDurationEvents(this);
            }, 60000);
        }

        return result;
    };
})();

(function () {
    "use strict";

    const RESET_WARNINGS = {
        equipements: "Tous les équipements seront supprimés, ainsi que les interventions, rapports, déclarations de panne et documents d'équipement qui en dépendent.",
        interventions: "Toutes les interventions et leurs rapports seront supprimés. Les déclarations liées seront détachées et remises en attente.",
        rapports: "Tous les rapports seront supprimés. Les interventions clôturées par ces rapports seront remises en cours.",
        declarations: "Toutes les déclarations de panne et leurs photos enregistrées en base seront supprimées."
    };

    function addAdminResetStyles() {
        if (document.getElementById("adminResetStyles")) return;

        const style = document.createElement("style");
        style.id = "adminResetStyles";
        style.textContent = `
            .admin-reset-actions {
                position: relative;
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 5px;
            }
            .admin-reset-toggle {
                border: 0;
                background: transparent;
                color: #dc2626;
                cursor: pointer;
                font-size: 12px;
                font-weight: 700;
                padding: 0;
            }
            .admin-reset-toggle:hover { text-decoration: underline; }
            .admin-reset-panel {
                display: none;
                position: absolute;
                top: calc(100% + 10px);
                right: 0;
                width: min(360px, 90vw);
                padding: 16px;
                background: #fff;
                border: 1px solid #fecaca;
                border-radius: 14px;
                box-shadow: 0 18px 45px rgba(15, 23, 42, .18);
                z-index: 5000;
                text-align: left;
            }
            .admin-reset-panel.open { display: block; }
            .admin-reset-panel h3 {
                margin: 0 0 10px;
                color: #991b1b;
                font-size: 16px;
            }
            .admin-reset-panel select {
                width: 100%;
                margin: 0 0 10px;
                padding: 10px 12px;
                border: 1px solid #cbd5e1;
                border-radius: 9px;
                background: #fff;
            }
            .admin-reset-warning {
                min-height: 42px;
                margin: 0 0 12px;
                color: #7f1d1d;
                font-size: 12px;
                line-height: 1.45;
            }
            .admin-reset-confirm {
                width: 100%;
                border: 0;
                border-radius: 9px;
                padding: 10px 12px;
                background: #dc2626;
                color: #fff;
                font-weight: 700;
                cursor: pointer;
            }
            .admin-reset-confirm:hover { background: #b91c1c; }
            .admin-reset-success {
                margin: 0 0 18px;
                padding: 12px 14px;
                border: 1px solid #bbf7d0;
                border-radius: 12px;
                background: #f0fdf4;
                color: #166534;
                font-weight: 700;
            }
        `;
        document.head.appendChild(style);
    }

    function showResetSuccessIfNeeded() {
        const params = new URLSearchParams(window.location.search);
        if (!params.get("reset_done")) return;

        const main = document.querySelector("main.main");
        if (main) {
            const banner = document.createElement("div");
            banner.className = "admin-reset-success";
            banner.textContent = "Réinitialisation terminée.";
            main.prepend(banner);
        }

        params.delete("reset_done");
        const query = params.toString();
        const cleanUrl = window.location.pathname + (query ? `?${query}` : "") + window.location.hash;
        window.history.replaceState({}, "", cleanUrl);
    }

    function initAdminResetControl() {
        if (!document.body.classList.contains("role-admin")) return;
        if (document.getElementById("adminResetControl")) return;

        const logoutLink = document.querySelector(".tabs-user .tabs-logout");
        if (!logoutLink || !logoutLink.parentElement) return;

        addAdminResetStyles();

        const parent = logoutLink.parentElement;
        const actions = document.createElement("div");
        actions.className = "admin-reset-actions";
        actions.id = "adminResetControl";
        parent.insertBefore(actions, logoutLink);
        actions.appendChild(logoutLink);

        const toggle = document.createElement("button");
        toggle.type = "button";
        toggle.className = "admin-reset-toggle";
        toggle.textContent = "Réinitialiser des données";
        actions.appendChild(toggle);

        const panel = document.createElement("div");
        panel.className = "admin-reset-panel";
        panel.innerHTML = `
            <h3>Réinitialiser des données</h3>
            <form method="POST" action="/admin/reset-data" id="adminResetForm">
                <select name="reset_target" id="adminResetTarget" required>
                    <option value="">Choisir les données à réinitialiser</option>
                    <option value="equipements">Équipements</option>
                    <option value="interventions">Interventions</option>
                    <option value="rapports">Rapports</option>
                    <option value="declarations">Déclarations de panne</option>
                </select>
                <p class="admin-reset-warning" id="adminResetWarning">
                    Sélectionne une catégorie pour afficher les conséquences de la réinitialisation.
                </p>
                <button type="submit" class="admin-reset-confirm">Confirmer</button>
            </form>
        `;
        actions.appendChild(panel);

        const select = panel.querySelector("#adminResetTarget");
        const warning = panel.querySelector("#adminResetWarning");
        const form = panel.querySelector("#adminResetForm");

        toggle.addEventListener("click", function (event) {
            event.stopPropagation();
            panel.classList.toggle("open");
        });

        panel.addEventListener("click", function (event) {
            event.stopPropagation();
        });

        select.addEventListener("change", function () {
            warning.textContent = RESET_WARNINGS[select.value]
                || "Sélectionne une catégorie pour afficher les conséquences de la réinitialisation.";
        });

        form.addEventListener("submit", function (event) {
            const target = select.value;
            if (!target) {
                event.preventDefault();
                select.focus();
                return;
            }

            const label = select.options[select.selectedIndex].textContent;
            const confirmed = window.confirm(
                `Confirmer la réinitialisation de : ${label} ?\n\n${RESET_WARNINGS[target]}\n\nCette action est irréversible.`
            );

            if (!confirmed) {
                event.preventDefault();
            }
        });

        document.addEventListener("click", function () {
            panel.classList.remove("open");
        });

        showResetSuccessIfNeeded();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initAdminResetControl);
    } else {
        initAdminResetControl();
    }
})();
