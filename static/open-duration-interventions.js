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

    function cloneEventData(sourceEvent, segment, index) {
        const props = sourceEvent.extendedProps || {};
        const originalId = props.orig_id;

        return {
            id: `open-${originalId}-${index}`,
            title: sourceEvent.title,
            start: segment.start,
            end: segment.end,
            backgroundColor: sourceEvent.backgroundColor,
            borderColor: sourceEvent.borderColor,
            textColor: sourceEvent.textColor,
            extendedProps: {
                ...props,
                open_duration: true
            }
        };
    }

    async function loadDetails(calendar, interventionId) {
        calendar.__gmaoOpenDurationDetails = calendar.__gmaoOpenDurationDetails || new Map();

        if (calendar.__gmaoOpenDurationDetails.has(interventionId)) {
            return calendar.__gmaoOpenDurationDetails.get(interventionId);
        }

        try {
            const response = await fetch(`/interventions/${interventionId}/details`, {
                credentials: "same-origin"
            });

            if (!response.ok) {
                return null;
            }

            const details = await response.json();
            calendar.__gmaoOpenDurationDetails.set(interventionId, details);
            return details;
        } catch (error) {
            console.error("Impossible de charger l'intervention à durée ouverte", error);
            return null;
        }
    }

    async function refreshOpenDurationEvents(calendar) {
        if (!calendar || calendar.__gmaoOpenDurationRefreshing) {
            return;
        }

        calendar.__gmaoOpenDurationRefreshing = true;

        try {
            const allEvents = calendar.getEvents();
            const originals = new Map();

            for (const event of allEvents) {
                const props = event.extendedProps || {};
                const originalId = props.orig_id;

                if (!originalId || props.open_duration) {
                    continue;
                }

                if (!originals.has(originalId)) {
                    originals.set(originalId, event);
                }
            }

            const now = new Date();

            for (const [interventionId, sourceEvent] of originals.entries()) {
                const details = await loadDetails(calendar, interventionId);
                if (!details) continue;

                const duration = Number(details.estimated_duration || 0);
                const status = String(details.status || "").toLowerCase();

                // Seules les interventions sans durée connue restent ouvertes.
                if (duration > 0 || !["planned", "in_progress"].includes(status)) {
                    continue;
                }

                const start = parseInterventionStart(details);
                if (!start) continue;

                // Tant que l'heure de départ n'est pas atteinte, on conserve le
                // bloc de planification d'origine pour matérialiser le rendez-vous.
                if (now <= start) {
                    continue;
                }

                const priority = String(details.priority || sourceEvent.extendedProps.priority || "").toLowerCase();
                const critical = priority === "critical";
                const segments = buildWorkingSegments(start, now, critical);

                // Supprime le bloc provisoire de 60 min généré côté serveur ainsi
                // que les éventuels segments ouverts d'un rafraîchissement précédent.
                for (const event of calendar.getEvents()) {
                    const props = event.extendedProps || {};
                    if (String(props.orig_id) === String(interventionId)) {
                        event.remove();
                    }
                }

                segments.forEach((segment, index) => {
                    calendar.addEvent(cloneEventData(sourceEvent, segment, index));
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
