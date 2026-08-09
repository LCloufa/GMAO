(() => {
    const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
    let deferredInstallPrompt = null;

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

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', installStockPurchaseShortcuts);
    } else {
        installStockPurchaseShortcuts();
    }

    if ('serviceWorker' in navigator) {
        window.addEventListener('load', () => {
            navigator.serviceWorker
                .register('/static/service-worker.js', { scope: '/' })
                .catch((error) => console.warn('Service worker GMAO non enregistré :', error));
        });
    }
})();
