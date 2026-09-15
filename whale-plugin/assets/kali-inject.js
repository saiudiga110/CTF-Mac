/**
 * Kali Linux Machine — Navbar Button Injection
 * Injects a single modern Kali button into the right side of the navbar.
 * Only activates on /challenges page.
 */
(function () {
    'use strict';

    // Only run on challenges page
    if (!window.location.pathname.match(/^\/challenges\/?$/)) return;

    document.addEventListener('DOMContentLoaded', init);
    if (document.readyState !== 'loading') init();

    var kaliUrl = null;
    var injected = false;

    function init() {
        if (injected) return;
        injected = true;
        injectNavButton();
        checkKaliStatus();
    }

    function injectNavButton() {
        // Find the navbar-nav on the right side
        var navbars = document.querySelectorAll('.navbar-nav');
        var rightNav = null;

        // The right navbar typically has profile/settings links
        for (var i = 0; i < navbars.length; i++) {
            var nav = navbars[i];
            if (nav.querySelector('a[href*="settings"], a[href*="profile"], a[href*="notifications"]')) {
                rightNav = nav;
                break;
            }
        }

        if (!rightNav) {
            // Fallback: use the last navbar-nav
            rightNav = navbars[navbars.length - 1];
        }

        if (!rightNav) return;

        // Create the Kali nav item
        var li = document.createElement('li');
        li.className = 'nav-item';
        li.id = 'kali-nav-item';
        li.innerHTML =
            '<a class="nav-link kali-nav-btn" href="javascript:void(0)" id="kali-nav-link" title="Kali Linux Machine">' +
            '  <span class="kali-btn-icon">⌨</span>' +
            '  <span class="kali-btn-text" id="kali-btn-text">Kali Linux</span>' +
            '  <span class="kali-btn-dot" id="kali-btn-dot"></span>' +
            '</a>';

        // Insert as the first item in the right nav
        rightNav.insertBefore(li, rightNav.firstChild);

        // Click handler
        document.getElementById('kali-nav-link').addEventListener('click', handleKaliClick);
    }

    function getCsrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) return meta.getAttribute('content');
        if (window.init && window.init.csrfNonce) return window.init.csrfNonce;
        return '';
    }

    function apiFetch(url, opts) {
        opts = opts || {};
        opts.credentials = 'same-origin';
        opts.headers = opts.headers || {};
        opts.headers['CSRF-Token'] = getCsrfToken();
        opts.headers['Content-Type'] = opts.headers['Content-Type'] || 'application/json';
        return fetch(url, opts).then(function (r) { return r.json(); });
    }

    function checkKaliStatus() {
        apiFetch('/plugins/ctfd-whale/kali/status', { method: 'GET' })
            .then(function (data) {
                if (!data.success) return;
                updateBtn(data.status, data.url);
            })
            .catch(function () { });
    }

    function updateBtn(status, url) {
        var textEl = document.getElementById('kali-btn-text');
        var dotEl = document.getElementById('kali-btn-dot');
        var linkEl = document.getElementById('kali-nav-link');

        if (!textEl || !dotEl) return;

        if (status === 'running') {
            kaliUrl = url;
            textEl.textContent = 'Open Kali';
            dotEl.className = 'kali-btn-dot kali-dot-on';
            linkEl.classList.add('kali-running');
            linkEl.title = 'Click to open your Kali Linux desktop';
        } else if (status === 'not_found') {
            kaliUrl = null;
            textEl.textContent = 'Kali Linux';
            dotEl.className = 'kali-btn-dot';
            linkEl.classList.remove('kali-running');
            linkEl.title = 'Click to launch your Kali Linux machine';
        } else {
            kaliUrl = null;
            textEl.textContent = 'Kali Linux';
            dotEl.className = 'kali-btn-dot kali-dot-warn';
            linkEl.classList.remove('kali-running');
            linkEl.title = 'Kali machine is ' + status + '. Click to restart.';
        }
    }

    function handleKaliClick(e) {
        e.preventDefault();
        if (kaliUrl) {
            // Already running — open in new tab
            window.open(kaliUrl, 'kali-desktop');
            return;
        }
        // Not running — start it
        startKali();
    }

    function startKali() {
        var textEl = document.getElementById('kali-btn-text');
        var linkEl = document.getElementById('kali-nav-link');
        if (textEl) textEl.textContent = 'Starting...';
        if (linkEl) linkEl.classList.add('kali-loading');

        apiFetch('/plugins/ctfd-whale/kali/start', {
            method: 'POST',
            body: JSON.stringify({})
        }).then(function (data) {
            if (linkEl) linkEl.classList.remove('kali-loading');
            if (data.success && data.url) {
                kaliUrl = data.url;
                updateBtn('running', kaliUrl);
                // Open immediately in new tab
                window.open(kaliUrl, 'kali-desktop');
            } else {
                if (textEl) textEl.textContent = 'Kali Linux';
                alert(data.msg || 'Failed to start Kali machine. Please try again.');
            }
        }).catch(function () {
            if (linkEl) linkEl.classList.remove('kali-loading');
            if (textEl) textEl.textContent = 'Kali Linux';
            alert('Network error. Please try again.');
        });
    }
})();
