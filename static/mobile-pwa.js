(() => {
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    let deferredInstallPrompt = null;

    function installBrandAssets() {
        const brandMark = document.querySelector('.tabs-brand-mark');
        if (brandMark) {
            const image = document.createElement('img');
            image.src = '/static/brand-logo.png?v=3';
            image.alt = 'GMAO Pro';
            image.width = 34;
            image.height = 34;
            image.style.width = '100%';
            image.style.height = '100%';
            image.style.display = 'block';
            image.style.objectFit = 'cover';
            image.style.borderRadius = 'inherit';
            brandMark.replaceChildren(image);
            brandMark.style.background = 'transparent';
            brandMark.style.color = 'transparent';
        }

        const favicon = document.querySelector('link[rel="icon"]');
        if (favicon) {
            favicon.href = '/static/app-icon-192.png?v=3';
            favicon.type = 'image/png';
        }

        const appleIcon = document.querySelector('link[rel="apple-touch-icon"]');
        if (appleIcon) {
            appleIcon.href = '/static/app-icon-180.png?v=3';
        }

        const manifest = document.querySelector('link[rel="manifest"]');
        if (manifest) {
            manifest.href = '/static/manifest.webmanifest?v=3';
        }
    }

    function closeMobileMore() {
        document.getElementById('mobileMoreSheet')?.classList.remove('open');
        document.getElementById('mobileMoreBackdrop')?.classList.remove('open');
        document.body.style.overflow = '';
    }

    function openMobileMore() {
        document.getElementById('mobileMoreSheet')?.classList.add('open');
        document.getElementById('mobileMoreBackdrop')?.classList.add('open');
        document.body.style.overflow = 'hidden';
    }

    window.toggleMobileMore = function () {
        const sheet = document.getElementById('mobileMoreSheet');
        if (!sheet) return;
        if (sheet.classList.contains('open')) closeMobileMore();
        else openMobileMore();
    };

    window.closeMobileMore = closeMobileMore;

    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeMobileMore();
    });

    window.addEventListener('beforeinstallprompt', (event) => {
        event.preventDefault();
        deferredInstallPrompt = event;
        const button = document.getElementById('pwaInstallButton');
        if (button && !isStandalone) button.hidden = false;
    });

    window.installGmaoPwa = async function () {
        if (!deferredInstallPrompt) return;
        deferredInstallPrompt.prompt();
        try {
            await deferredInstallPrompt.userChoice;
        } finally {
            deferredInstallPrompt = null;
            const button = document.getElementById('pwaInstallButton');
            if (button) button.hidden = true;
        }
    };

    const ua = navigator.userAgent || '';
    const isiOS = /iPad|iPhone|iPod/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    const iosHint = document.getElementById('pwaIosInstallHint');
    if (iosHint && isiOS && !isStandalone) iosHint.hidden = false;

    function installStockPurchaseShortcuts() {
        if (!window.location.pathname.startsWith('/stock')) return;
        const header = document.querySelector('.stock-header');
        if (!header || document.getElementById('stockPurchaseShortcuts')) return;

        let actions = header.querySelector('.stock-header-actions');
        if (!actions) {
            actions = document.createElement('div');
            actions.className = 'stock-header-actions';
            header.appendChild(actions);
        }

        const wrap = document.createElement('span');
        wrap.id = 'stockPurchaseShortcuts';
        wrap.style.display = 'contents';
        const links = [
            ['/stock/achats', '🧾 Commandes'],
            ['/stock/reapprovisionnement', '🔄 Réappro'],
            ['/stock/demandes-achat', '🛒 Demandes'],
            ['/stock/fournisseurs/infos-achats', '🏢 Paramètres fournisseurs'],
        ];
        for (const [href, label] of links) {
            if (actions.querySelector(`a[href="${href}"]`)) continue;
            const link = document.createElement('a');
            link.href = href;
            link.className = 'stock-btn-secondary';
            link.textContent = label;
            wrap.appendChild(link);
        }
        actions.appendChild(wrap);
    }

    const STOCK_RESET_WARNING = "Supprime définitivement tout le périmètre Stock/Achats : articles, catégories, emplacements, mouvements, réservations, consommations liées aux interventions, fournisseurs, associations article/fournisseur, paramètres achats, demandes d'achat, bons de commande, lignes et réceptions. Les interventions, rapports, équipements, clients, techniciens et utilisateurs sont conservés.";

    function installStockResetOption() {
        const select = document.getElementById('adminResetTarget');
        const form = document.getElementById('adminResetForm');
        const warning = document.getElementById('adminResetWarning');
        if (!select || !form || !warning) return;
        if (select.querySelector('option[value="stock"]')) return;

        const option = document.createElement('option');
        option.value = 'stock';
        option.textContent = 'Stock complet + fournisseurs + achats/commandes';
        select.appendChild(option);

        select.addEventListener('change', () => {
            if (select.value === 'stock') {
                warning.textContent = STOCK_RESET_WARNING;
            }
        });

        // Le contrôle global historique possède sa propre confirmation. Pour
        // la nouvelle cible stock, on intercepte uniquement cette soumission
        // afin d'afficher le bon avertissement au lieu d'un message générique.
        form.addEventListener('submit', (event) => {
            if (select.value !== 'stock') return;

            event.preventDefault();
            event.stopImmediatePropagation();

            const confirmed = window.confirm(
                `Confirmer la réinitialisation de : ${option.textContent} ?\n\n${STOCK_RESET_WARNING}\n\nCette action est irréversible.`
            );
            if (confirmed) {
                HTMLFormElement.prototype.submit.call(form);
            }
        }, true);
    }

    function initEnhancements() {
        installBrandAssets();
        installStockPurchaseShortcuts();
        installStockResetOption();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initEnhancements);
    } else {
        initEnhancements();
    }

    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker
                .register('/static/service-worker.js', { scope: '/' })
                .catch((error) => console.warn('Service worker GMAO non enregistré :', error));
        });
    }
})();
