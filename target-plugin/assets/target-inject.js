/**
 * Lloyds CTF branding + per-user target and Linux Pwn Machine controls on /challenges.
 * Also provides global live events: first blood toasts, admin broadcasts,
 * target restart alerts, and challenge difficulty ratings.
 * v37
 */
(function () {
    'use strict';

    var CTF_NAME = 'Lloyds CTF';
    var POLL_INTERVAL = 30000;
    var INSTANCE_DURATION = 3600;

    // =========================================================================
    // GLOBAL LIVE EVENTS  (runs on every page, not just /challenges)
    // =========================================================================

    var _seenBroadcasts  = {};   // id â†’ true, persisted to sessionStorage
    var _seenFirstBloods = {};   // id â†’ true
    var _currentRatings  = {};   // chal_id â†’ {avg, count}
    var _LIVE_POLL_MS    = 12000;

    // Restore seen-IDs from session so page refreshes don't re-show old toasts
    try {
        var _sb = JSON.parse(sessionStorage.getItem('ctf_seen_bc')  || '{}');
        var _sf = JSON.parse(sessionStorage.getItem('ctf_seen_fb')  || '{}');
        _seenBroadcasts  = _sb;
        _seenFirstBloods = _sf;
    } catch(e) {}

    function _saveSeen() {
        try {
            sessionStorage.setItem('ctf_seen_bc', JSON.stringify(_seenBroadcasts));
            sessionStorage.setItem('ctf_seen_fb', JSON.stringify(_seenFirstBloods));
        } catch(e) {}
    }

    function _getCsrf() {
        var m = document.querySelector('meta[name="csrf-token"]');
        if (m) return m.getAttribute('content');
        if (window.init && window.init.csrfNonce) return window.init.csrfNonce;
        return '';
    }

    // â”€â”€ Toast container (shared by all toast types) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    function _getToastContainer() {
        var el = document.getElementById('ctf-toast-container');
        if (el) return el;
        el = document.createElement('div');
        el.id = 'ctf-toast-container';
        el.style.cssText =
            'position:fixed;bottom:24px;right:24px;z-index:99999;' +
            'display:flex;flex-direction:column;gap:10px;pointer-events:none;max-width:380px;';
        document.body.appendChild(el);
        return el;
    }

    function _showToast(opts) {
        // opts: {color, bg, border, icon, title, body, duration}
        var color  = opts.color  || '#10b981';
        var bg     = opts.bg     || 'rgba(16,185,129,0.08)';
        var border = opts.border || 'rgba(16,185,129,0.3)';
        var duration = opts.duration || 7000;

        var toast = document.createElement('div');
        toast.className = 'ctf-toast';
        toast.style.background = bg;
        toast.style.border = '1px solid ' + border;
        toast.style.borderLeft = '4px solid ' + color;
        toast.innerHTML =
            '<span class="ctf-toast-icon">' + opts.icon + '</span>' +
            '<div style="flex:1;min-width:0;">' +
              '<div class="ctf-toast-title" style="color:' + color + ';">' + opts.title + '</div>' +
              '<div class="ctf-toast-text">' + opts.body + '</div>' +
            '</div>';

        var dismiss = function() {
            toast.style.transition = 'opacity .25s, transform .25s';
            toast.style.opacity = '0';
            toast.style.transform = 'translateX(110%)';
            setTimeout(function() { toast.parentNode && toast.parentNode.removeChild(toast); }, 260);
        };
        toast.addEventListener('click', dismiss);

        _getToastContainer().appendChild(toast);
        setTimeout(dismiss, duration);
        return toast;
    }

    // â”€â”€ First blood â€” dramatic full-width flash â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    function _showFirstBlood(data) {
        // Full-screen flash overlay
        var overlay = document.createElement('div');
        overlay.style.cssText =
            'position:fixed;inset:0;z-index:999990;pointer-events:none;' +
            'display:flex;align-items:center;justify-content:center;' +
            'background:rgba(255,20,20,0.12);' +
            'animation:fb-flash .6s ease-out forwards;';
        overlay.innerHTML =
            '<div class="ctf-fb-card">' +
            '<div style="font-size:1.8rem;margin-bottom:8px;">ðŸ©¸</div>' +
            '<div class="ctf-fb-kicker">First blood</div>' +
            '<div style="font-size:.95rem;color:var(--text);margin-bottom:4px;">' +
            '<strong>' + _esc(data.solver) + '</strong> solved <strong>' + _esc(data.challenge) + '</strong></div>' +
            '<div style="font-size:.75rem;color:var(--muted);">' +
            _esc(data.category) + ' Â· ' + data.points + ' pts</div>' +
            '</div>';
        document.body.appendChild(overlay);

        // Inject keyframes once
        if (!document.getElementById('ctf-fb-style')) {
            var s = document.createElement('style');
            s.id = 'ctf-fb-style';
            s.textContent = '@keyframes fb-flash{0%{opacity:0}20%{opacity:1}80%{opacity:1}100%{opacity:0}}' +
                '@keyframes toast-slide-in{from{transform:translateX(110%);opacity:0}to{transform:none;opacity:1}}';
            document.head.appendChild(s);
        }
        setTimeout(function() { overlay.parentNode && overlay.parentNode.removeChild(overlay); }, 3500);

        // Also show a persistent toast
        _showToast({
            color: '#ff4444', bg: 'rgba(255,68,68,.08)', border: 'rgba(255,68,68,.35)',
            icon: 'ðŸ©¸', title: 'First Blood',
            body: '<strong>' + _esc(data.solver) + '</strong> solved <em>' + _esc(data.challenge) + '</em>',
            duration: 12000,
        });
    }

    // â”€â”€ Broadcast toast â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    function _showBroadcast(data) {
        _showToast({
            color: '#00d4ff', bg: 'rgba(0,212,255,.08)', border: 'rgba(0,212,255,.3)',
            icon: 'ðŸ“¢', title: 'Admin Announcement',
            body: _esc(data.message),
            duration: 15000,
        });
    }

    // â”€â”€ Restart alert â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    function _showRestartAlert(data) {
        var label = data.role === 'kali' ? 'Pwn Machine' : 'Target';
        _showToast({
            color: '#ffcc00', bg: 'rgba(255,204,0,.07)', border: 'rgba(255,204,0,.3)',
            icon: 'âš¡', title: label + ' Auto-Restarted',
            body: 'Your ' + label + ' crashed and was automatically restarted.',
            duration: 10000,
        });
    }

    // â”€â”€ Star rating prompt (challenges page only, called after solve detected) â”€
    var _ratingQueue  = [];   // [{id, name}]
    var _ratingActive = false;

    function _queueRating(challenges) {
        challenges.forEach(function(c) { _ratingQueue.push(c); });
        if (!_ratingActive) _showNextRating();
    }

    function _showNextRating() {
        if (!_ratingQueue.length) { _ratingActive = false; return; }
        _ratingActive = true;
        var chal = _ratingQueue.shift();
        _showRatingModal(chal);
    }

    function _showRatingModal(chal) {
        var existing = document.getElementById('ctf-rating-modal');
        if (existing) existing.parentNode.removeChild(existing);

        var modal = document.createElement('div');
        modal.id = 'ctf-rating-modal';
        modal.style.cssText =
            'position:fixed;inset:0;z-index:999980;display:flex;align-items:center;justify-content:center;' +
            'background:rgba(0,0,0,.7);animation:toast-slide-in .3s ease;';
        modal.innerHTML =
            '<div class="ctf-rating-card">' +
            '<div style="font-size:1.6rem;margin-bottom:8px;">â­</div>' +
            '<div class="ctf-rating-kicker">Challenge solved</div>' +
            '<div class="ctf-rating-name">' + _esc(chal.name) + '</div>' +
            '<div class="ctf-rating-hint">How hard was it?</div>' +
            '<div id="ctf-stars" style="display:flex;justify-content:center;gap:8px;margin-bottom:18px;">' +
            [1,2,3,4,5].map(function(n) {
                return '<span data-star="'+n+'" style="font-size:2rem;cursor:pointer;transition:.15s;color:#334155;' +
                    '" onmouseenter="_ctfStarHover('+n+')" onmouseleave="_ctfStarLeave()" ' +
                    'onclick="_ctfStarClick('+chal.id+','+n+')">â˜…</span>';
            }).join('') +
            '</div>' +
            '<button type="button" class="ctf-rating-skip" onclick="_ctfRatingSkip()">Skip</button>' +
            '</div>';
        document.body.appendChild(modal);
    }

    window._ctfStarHover = function(n) {
        var stars = document.querySelectorAll('#ctf-stars [data-star]');
        stars.forEach(function(s) {
            s.style.color = parseInt(s.dataset.star) <= n ? '#ffcc00' : '#333';
            s.style.transform = parseInt(s.dataset.star) <= n ? 'scale(1.2)' : '';
        });
    };
    window._ctfStarLeave = function() {
        document.querySelectorAll('#ctf-stars [data-star]').forEach(function(s) {
            s.style.color = '#334155'; s.style.transform = '';
        });
    };
    window._ctfStarClick = function(chalId, stars) {
        var modal = document.getElementById('ctf-rating-modal');
        if (modal) {
            var hint = modal.querySelector('.ctf-rating-hint');
            if (hint) hint.textContent = 'Thanks for rating';
            setTimeout(function() { modal.parentNode && modal.parentNode.removeChild(modal); _showNextRating(); }, 1200);
        }
        fetch('/plugins/ctf-live/rating', {
            method: 'POST', credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'CSRF-Token': _getCsrf()},
            body: JSON.stringify({challenge_id: chalId, rating: stars}),
        }).then(function(r) { return r.json(); })
          .then(function(d) { if (d.success) _overlayRating(String(chalId), d.avg, d.count); })
          .catch(function(){});
    };
    window._ctfRatingSkip = function() {
        var modal = document.getElementById('ctf-rating-modal');
        if (modal) modal.parentNode.removeChild(modal);
        _showNextRating();
    };

    // â”€â”€ Overlay star ratings on challenge cards â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    function _overlayAllRatings(ratings) {
        _currentRatings = ratings;
        Object.keys(ratings).forEach(function(cid) {
            _overlayRating(cid, ratings[cid].avg, ratings[cid].count);
        });
    }

    function _overlayRating(cid, avg, count) {
        // CTFd challenge buttons have :value="c.id" (Alpine.js bound)
        // We inject a small badge into each matching card
        var btns = document.querySelectorAll('.challenge-button[value="'+cid+'"]');
        if (!btns.length) btns = document.querySelectorAll('[data-cid="'+cid+'"]');
        btns.forEach(function(btn) {
            var old = btn.querySelector('.ctf-rating-badge');
            if (old) old.parentNode.removeChild(old);
            var badge = document.createElement('div');
            badge.className = 'ctf-rating-badge';
            badge.style.cssText =
                'position:absolute;bottom:6px;right:6px;font-size:.62rem;' +
                'font-family:\'Share Tech Mono\',monospace;color:#ffcc00;' +
                'background:rgba(0,0,0,.5);border-radius:3px;padding:1px 5px;pointer-events:none;';
            badge.textContent = 'â˜… ' + avg.toFixed(1) + ' (' + count + ')';
            // Make parent relative if needed
            if (getComputedStyle(btn).position === 'static') btn.style.position = 'relative';
            btn.appendChild(badge);
        });
    }

    // â”€â”€ Inject ratings via MutationObserver (Alpine.js renders async) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    function _watchChallengeCards() {
        var observer = new MutationObserver(function() {
            _overlayAllRatings(_currentRatings);
        });
        var target = document.querySelector('#challenges-container, .challenges-row, main');
        if (target) observer.observe(target, {childList: true, subtree: true});
    }

    // â”€â”€ Poll live events â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    function _pollLiveEvents() {
        fetch('/plugins/ctf-live/events', {
            credentials: 'same-origin',
            headers: {'CSRF-Token': _getCsrf()},
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            // First bloods
            (d.first_bloods || []).forEach(function(fb) {
                if (!_seenFirstBloods[fb.id]) {
                    _seenFirstBloods[fb.id] = true;
                    _showFirstBlood(fb);
                }
            });

            // Admin broadcasts
            (d.broadcasts || []).forEach(function(bc) {
                if (!_seenBroadcasts[bc.id]) {
                    _seenBroadcasts[bc.id] = true;
                    _showBroadcast(bc);
                }
            });

            // Restart alert
            if (d.restart_alert) {
                _showRestartAlert(d.restart_alert);
            }

            // Rating prompts (challenges page only)
            if (d.newly_solved && d.newly_solved.length && window.location.pathname.match(/^\/challenges/)) {
                _queueRating(d.newly_solved);
            }

            // Overlay ratings on challenge cards
            if (d.ratings && Object.keys(d.ratings).length) {
                _overlayAllRatings(d.ratings);
            }

            _saveSeen();
        })
        .catch(function() {});
    }

    function _esc(s) {
        return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }

    function _isAdminUi() {
        var p = window.location.pathname || '';
        return p.indexOf('/admin') !== -1 ||
            p.indexOf('/plugins/ctfd-target/admin') !== -1 ||
            p.indexOf('/plugins/ctf-live/admin') !== -1 ||
            p.indexOf('/api/ctf/') !== -1 ||
            p.indexOf('/api/mission') !== -1 ||
            !!document.getElementById('lloyds-admin-sidebar');
    }

    function _fmtRemain(seconds) {
        if (seconds < 0) seconds = 0;
        var h = Math.floor(seconds / 3600);
        var m = Math.floor((seconds % 3600) / 60);
        var s = seconds % 60;
        return (h < 10 ? '0' : '') + h + ':' + (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
    }

    function _renderMaintBanner(d) {
        if (_isAdminUi()) return;
        var msg = ((d && d.message) || '').trim();
        var now = (d && d.now) || Math.floor(Date.now() / 1000);
        if (!msg) {
            if (d && d.phase === 'countdown' && d.start) {
                msg = 'Event starts in ' + _fmtRemain(d.start - now);
            } else if (d && d.phase === 'ended') {
                msg = 'The event has ended.';
            } else if (d && d.paused) {
                msg = 'The CTF is paused. Challenges are hidden for now.';
            } else if (d && d.end && now < d.end && d.phase === 'live') {
                var left = d.end - now;
                if (left <= 900) msg = 'Event ends in ' + _fmtRemain(left);
            }
        }
        var existing = document.getElementById('ctf-maint-banner');
        if (!msg) {
            if (existing) existing.remove();
            document.body.classList.remove('has-maint-banner');
            return;
        }
        if (!existing) {
            existing = document.createElement('div');
            existing.id = 'ctf-maint-banner';
            existing.setAttribute('role', 'status');
            document.body.insertBefore(existing, document.body.firstChild);
        }
        existing.textContent = msg;
        document.body.classList.add('has-maint-banner');
    }

    function _pollBanner() {
        if (_isAdminUi()) return;
        fetch('/plugins/ctfd-target/banner', { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(_renderMaintBanner)
            .catch(function () {});
    }

    // Start global polling immediately (works on every page)
    _pollLiveEvents();
    setInterval(_pollLiveEvents, _LIVE_POLL_MS);
    _pollBanner();
    setInterval(_pollBanner, 10000);

    (function () {
        if (document.getElementById('ctf-chrome-v35')) return;
        var s = document.createElement('style');
        s.id = 'ctf-chrome-v35';
        s.textContent =
            '#ctf-countdown-wrap{background:rgba(16,185,129,.08)!important;border-color:rgba(16,185,129,.35)!important;border-radius:9px!important}' +
            '#ctf-countdown-label,#ctf-countdown-time{font-family:"JetBrains Mono",monospace!important;text-shadow:none!important;color:#10b981!important;letter-spacing:.06em!important}' +
            '#ctf-live-badge{background:rgba(16,185,129,.08)!important;border-color:rgba(16,185,129,.35)!important;color:#10b981!important;font-family:"JetBrains Mono",monospace!important;border-radius:999px!important}' +
            '#ctf-live-dot{background:#10b981!important;box-shadow:0 0 6px rgba(16,185,129,.4)!important}' +
            '#ctf-admin-bar{background:linear-gradient(180deg,rgba(22,32,54,.92),rgba(12,18,32,.88))!important;border-color:rgba(148,163,184,.12)!important;border-radius:14px!important}' +
            '.ctf-ab-badge,.ctf-ab-btn{font-family:"JetBrains Mono",monospace!important}' +
            '#ctf-chat-panel{background:rgba(10,16,30,.96)!important;border-color:rgba(148,163,184,.12)!important;border-radius:14px 14px 0 0!important}' +
            '#ctf-chat-header{font-family:Inter,system-ui,sans-serif!important;letter-spacing:0!important;text-transform:none!important}';
        (document.head || document.documentElement).appendChild(s);
    })();

    var _path = window.location.pathname || '';
    if (_path === '/' || _path === '') document.body.classList.add('ctf-home-page');
    if (/^\/challenges\/?$/.test(_path)) document.body.classList.add('ctf-challenges-page');
    if (/^\/scoreboard/.test(_path)) document.body.classList.add('ctf-scoreboard-page');
    if (/^\/(users|teams)\/?$/.test(_path)) document.body.classList.add('ctf-directory-page');
    if (/^\/(users|teams)\//.test(_path)) document.body.classList.add('ctf-profile-page');
    if (/^\/(login|register|reset_password)/.test(_path)) document.body.classList.add('ctf-auth-page');
    if (/^\/notifications/.test(_path)) document.body.classList.add('ctf-notifications-page');
    if (/^\/settings/.test(_path)) document.body.classList.add('ctf-settings-page');

    function _markActiveNav() {
        var here = (window.location.pathname || '/').replace(/\/+$/, '') || '/';
        document.querySelectorAll('.navbar .nav-link[href]').forEach(function (a) {
            var href = (a.getAttribute('href') || '').split('?')[0].replace(/\/+$/, '') || '/';
            if (href === '/' ) return;
            if (here === href || here.indexOf(href + '/') === 0) a.classList.add('active');
        });
    }
    function _markScoreboardMe() {
        if (!document.body.classList.contains('ctf-scoreboard-page')) return;
        var me = '';
        var toggle = document.querySelector('.navbar .dropdown-toggle, #navbarDropdown');
        if (toggle) me = (toggle.textContent || '').replace(/\s+/g, ' ').trim();
        if (!me) return;
        document.querySelectorAll('table tbody tr').forEach(function (tr) {
            var label = ((tr.querySelector('a') || tr.querySelector('td')) || {}).textContent || '';
            if (label.replace(/\s+/g, ' ').trim() === me) tr.classList.add('ctf-is-me');
        });
    }
    document.addEventListener('DOMContentLoaded', function () {
        _markActiveNav();
        _markScoreboardMe();
        setTimeout(_markScoreboardMe, 800);
    });
    if (document.readyState !== 'loading') {
        _markActiveNav();
        setTimeout(_markScoreboardMe, 400);
    }

    // Inject keyframe styles once
    (function() {
        if (document.getElementById('ctf-fb-style')) return;
        var s = document.createElement('style');
        s.id = 'ctf-fb-style';
        s.textContent =
            '@keyframes fb-flash{0%{opacity:0}20%{opacity:1}80%{opacity:1}100%{opacity:0}}' +
            '@keyframes toast-slide-in{from{transform:translateX(110%);opacity:0}to{transform:none;opacity:1}}';
        document.head.appendChild(s);
    })();

    // Watch challenge cards for Alpine.js late renders
    if (document.readyState !== 'loading') {
        setTimeout(_watchChallengeCards, 1000);
    } else {
        document.addEventListener('DOMContentLoaded', function() { setTimeout(_watchChallengeCards, 1000); });
    }

    function _polishChallengeModal() {
        var modal = document.querySelector('#challenge-window.show, #challenge-window.modal, #challenge-window, [x-ref="challengeWindow"], .challenge-window, .modal.show');
        if (!modal) return;
        if (!modal.querySelector('.challenge-name, .challenge-value, #challenge-submit, .challenge-submit')) return;
        modal.classList.add('ctf-chal-modal');

        var name = modal.querySelector('h2.challenge-name, h2.cyber-glitch, .challenge-name');
        var value = modal.querySelector('h3.challenge-value, .challenge-value');
        if (name && value && !name.closest('.ctf-chal-head') && !value.closest('.ctf-chal-head')) {
            var head = document.createElement('div');
            head.className = 'ctf-chal-head';
            name.parentNode.insertBefore(head, name);
            head.appendChild(name);
            head.appendChild(value);
        }

        var nodes = modal.querySelectorAll('a, button, summary, [role="button"]');
        for (var i = 0; i < nodes.length; i++) {
            var el = nodes[i];
            if (el.id === 'challenge-submit' || el.classList.contains('challenge-submit')) continue;
            var t = (el.textContent || '').replace(/\s+/g, ' ').trim();
            if (/^(Unlock Hint|View Hint)\b/i.test(t) || /Unlock Hint for/i.test(t)) {
                el.classList.add('ctf-hint-row');
            }
        }

        var submit = modal.querySelector('#challenge-submit, .challenge-submit');
        if (submit) {
            submit.classList.add('btn-primary');
            submit.classList.remove('btn-outline-secondary');
        }
        var input = modal.querySelector('#challenge-input, input[placeholder*="Flag"], input[name="submission"], input[name="answer"], .challenge-input');
        if (input && (!input.placeholder || /^Flag(\s+\d+)?$/i.test(input.placeholder.trim()))) {
            input.placeholder = 'LYD{â€¦}';
        }
        if (input) {
            var row = input.closest('.submit-row, .row') || input.parentElement;
            if (row) row.classList.add('ctf-submit-bar');
        }
    }

    if (/^\/challenges/.test(window.location.pathname || '')) {
        var _modalPolishTimer = null;
        var _modalPolishObs = new MutationObserver(function () {
            clearTimeout(_modalPolishTimer);
            _modalPolishTimer = setTimeout(_polishChallengeModal, 80);
        });
        if (document.body) _modalPolishObs.observe(document.body, { childList: true, subtree: true });
        document.addEventListener('shown.bs.modal', _polishChallengeModal);
        setTimeout(_polishChallengeModal, 800);
    }

    // =========================================================================
    // END GLOBAL SECTION â€” challenges-page-only code below
    // =========================================================================

    var targetUrl = null;
    var pwnUrl = null;
    var expiresAt = null;
    var pwnExpiresAt = null;
    var injected = false;
    var pollTimer = null;
    var countdownTimer = null;
    var pwnCountdownTimer = null;
    var _warnedTarget = {};
    var _warnedPwn = {};
    var _autoExtended = false;
    var _autoExtendedPwn = false;
    var _pwnPollingTimer = null;
    var _pwnPollingCount = 0;
    var _pwnPollingMax = 20;  // 20 polls Ã— 2s = 40s max wait
    var _pwnCreatedAt = null; // epoch seconds, set from kali/status
    var KALI_MAX_LIFETIME = 70 * 60; // 70 minutes in seconds

    function applyBranding() {
        if (document.title && document.title.indexOf(CTF_NAME) === -1) {
            document.title = document.title.replace(/CTFd/gi, CTF_NAME);
        }
        var brand = document.querySelector('.navbar-brand');
        if (brand) brand.textContent = CTF_NAME;
        document.querySelectorAll('footer a, .footer a, footer small, .footer small').forEach(function (el) {
            if (el && /Powered by CTFd/i.test(el.textContent || '')) {
                el.textContent = 'Lloyds CTF';
            }
        });
    }

    document.addEventListener('DOMContentLoaded', applyBranding);
    if (document.readyState !== 'loading') applyBranding();

    // â”€â”€ Theme toggle (dark â†” light, persisted, works on every page) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    var _SVG_SUN  = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="4.22" y1="4.22" x2="6.34" y2="6.34"/><line x1="17.66" y1="17.66" x2="19.78" y2="19.78"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/><line x1="4.22" y1="19.78" x2="6.34" y2="17.66"/><line x1="17.66" y1="6.34" x2="19.78" y2="4.22"/></svg>';
    var _SVG_MOON = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

    function _applyTheme(theme) {
        if (theme === 'light') {
            document.documentElement.setAttribute('data-theme', 'light');
        } else {
            document.documentElement.removeAttribute('data-theme');
        }
        var btn = document.getElementById('ctf-theme-toggle');
        if (btn) btn.innerHTML = theme === 'light' ? _SVG_MOON : _SVG_SUN;
    }

    // Apply immediately to avoid flash of wrong theme
    _applyTheme(localStorage.getItem('ctf-theme') || 'dark');

    function _initThemeToggle() {
        if (document.getElementById('ctf-theme-toggle')) return;
        var theme = localStorage.getItem('ctf-theme') || 'dark';
        // Try common CTFd navbar structures
        var nav = document.querySelector('.navbar-nav.ml-auto')
               || document.querySelector('.navbar-nav.ms-auto')
               || document.querySelector('nav ul.navbar-nav:last-of-type')
               || document.querySelector('.navbar-nav');
        if (!nav) return;
        var li = document.createElement('li');
        li.className = 'nav-item';
        li.innerHTML = '<button id="ctf-theme-toggle" title="Toggle light / dark mode">' +
            (theme === 'light' ? _SVG_MOON : _SVG_SUN) + '</button>';
        nav.appendChild(li);
        document.getElementById('ctf-theme-toggle').addEventListener('click', function () {
            var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
            _applyTheme(next);
            localStorage.setItem('ctf-theme', next);
        });
    }

    document.addEventListener('DOMContentLoaded', _initThemeToggle);
    if (document.readyState !== 'loading') _initThemeToggle();
    // â”€â”€ End theme toggle â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    if (!window.location.pathname.match(/^\/(challenges|dashboard)\/?$/)) return;

    fetch('/plugins/ctfd-target/target/mode', { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (d) {
            if (d.mode === 'per_challenge') {
                document.addEventListener('DOMContentLoaded', initPerChallenge);
                if (document.readyState !== 'loading') initPerChallenge();
                return;
            }
            document.addEventListener('DOMContentLoaded', init);
            if (document.readyState !== 'loading') init();
        })
        .catch(function () {
            document.addEventListener('DOMContentLoaded', init);
            if (document.readyState !== 'loading') init();
        });

    function getCsrfToken() {
        var m = document.querySelector('meta[name="csrf-token"]');
        if (m) return m.getAttribute('content');
        if (window.init && window.init.csrfNonce) return window.init.csrfNonce;
        return '';
    }

    function apiFetch(url, opts) {
        opts = opts || {};
        opts.credentials = 'same-origin';
        opts.headers = opts.headers || {};
        opts.headers['CSRF-Token'] = getCsrfToken();
        opts.headers['Content-Type'] = 'application/json';
        return fetch(url, opts).then(function (r) { return r.json(); });
    }

    function init() {
        if (injected) return;
        injected = true;
        document.body.classList.add('ctf-challenges-page');
        injectBanner();
        initMissionHud();
        refreshStatus();
        pollTimer = setInterval(refreshStatus, POLL_INTERVAL);
        // Expose globally so other scripts (extend button) can trigger a refresh
        window.ctfRefreshStatus = refreshStatus;
    }

    function injectBanner() {
        var banner = document.createElement('div');
        banner.id = 'target-instance-banner';
        banner.innerHTML =
            '<div class="instance-grid">' +

            '  <div class="instance-panel instance-panel-target">' +
            '    <div class="instance-panel-header">' +
            '      <div class="instance-header-left">' +
            '        <span class="instance-panel-title">vBank</span>' +
            '      </div>' +
            '    </div>' +
            '    <div class="instance-panel-body">' +
            '      <div class="instance-panel-status-wrap">' +
            '        <span class="instance-status-dot" id="target-dot"></span>' +
            '        <p class="instance-status-text" id="instance-status-text">Not running</p>' +
            '      </div>' +
            '      <div class="instance-url-row" id="instance-url-row" style="display:none;">' +
            '        <a class="instance-url-link" id="instance-url-link" href="#" target="_blank">Open vBank</a>' +
            '      </div>' +
            '      <div class="instance-timer-wrap" id="instance-timer-wrap" style="display:none;">' +
            '        <div class="instance-timer-label">Time left</div>' +
            '        <div class="instance-timer" id="instance-timer">60:00</div>' +
            '      </div>' +
            '    </div>' +
            '    <div class="instance-panel-actions">' +
            '      <button class="instance-btn instance-btn-launch" id="instance-btn-launch">Launch</button>' +
            '      <button class="instance-btn instance-btn-open" id="instance-btn-open" style="display:none;">Open</button>' +
            '      <button class="instance-btn instance-btn-copy" id="instance-btn-copy" style="display:none;">Copy URL</button>' +
            '      <button class="instance-btn instance-btn-destroy" id="instance-btn-destroy" style="display:none;">Stop</button>' +
            '      <button class="instance-btn instance-btn-reset" id="instance-btn-reset" style="display:none;">Reset</button>' +
            '    </div>' +
            '  </div>' +

            '  <div class="instance-panel instance-panel-pwn">' +
            '    <div class="instance-panel-header pwn-header">' +
            '      <div class="instance-header-left">' +
            '        <span class="instance-panel-title">Pwn Machine</span>' +
            '      </div>' +
            '    </div>' +
            '    <div class="instance-panel-body">' +
            '      <div class="instance-panel-status-wrap">' +
            '        <span class="instance-status-dot pwn-dot" id="pwn-dot"></span>' +
            '        <p class="instance-status-text" id="pwn-status-text">Not running</p>' +
            '      </div>' +
            '      <div class="instance-url-row" id="pwn-url-row" style="display:none;">' +
            '        <a class="instance-url-link" id="pwn-url-link" href="#" target="_blank">Open</a>' +
            '      </div>' +
            '      <div class="instance-timer-wrap" id="pwn-timer-wrap" style="display:none;">' +
            '        <div class="instance-timer-label">Time left</div>' +
            '        <div class="instance-timer pwn-timer-text" id="pwn-timer">60:00</div>' +
            '      </div>' +
            '    </div>' +
            '    <div class="instance-panel-actions">' +
            '      <button class="instance-btn instance-btn-launch pwn-btn-launch" id="pwn-btn-launch">Launch</button>' +
            '      <button class="instance-btn instance-btn-open pwn-btn-open" id="pwn-btn-open" style="display:none;">Open</button>' +
            '      <button class="instance-btn instance-btn-extend" id="pwn-btn-extend" style="display:none;">Extend 1 hour</button>' +
            '      <button class="instance-btn instance-btn-destroy" id="pwn-btn-destroy" style="display:none;">Stop</button>' +
            '    </div>' +
            '  </div>' +

            '</div>';

        var main = document.querySelector('main') || document.querySelector('[role="main"]');
        if (main) {
            main.insertAdjacentElement('afterbegin', banner);
        }

        document.getElementById('instance-btn-launch').addEventListener('click', launchTarget);
        document.getElementById('instance-btn-open').addEventListener('click', function () { if (targetUrl) window.open(targetUrl, '_blank'); });
        var copyBtn = document.getElementById('instance-btn-copy');
        if (copyBtn) copyBtn.addEventListener('click', copyTargetUrl);
        document.getElementById('instance-btn-destroy').addEventListener('click', destroyTarget);
        document.getElementById('instance-btn-reset').addEventListener('click', resetTarget);
        document.getElementById('pwn-btn-launch').addEventListener('click', launchPwn);
        document.getElementById('pwn-btn-open').addEventListener('click', openPwnSSO);
        document.getElementById('pwn-btn-extend').addEventListener('click', extendPwn);
        document.getElementById('pwn-btn-destroy').addEventListener('click', destroyPwn);
    }

    function refreshStatus() {
        apiFetch('/plugins/ctfd-target/target/status', { method: 'GET' }).then(updateTargetUI).catch(function () {});
        apiFetch('/plugins/ctfd-target/kali/status', { method: 'GET' }).then(updatePwnUI).catch(function () {});
    }

    function setTargetLoading(msg) {
        var btn = document.getElementById('instance-btn-launch');
        var text = document.getElementById('instance-status-text');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner"></span>' + (msg || 'Launching\u2026'); }
        if (text) text.textContent = msg || 'Launching target\u2026';
    }

    function setPwnLoading(msg) {
        var btn = document.getElementById('pwn-btn-launch');
        var text = document.getElementById('pwn-status-text');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner"></span>' + (msg || 'Launching\u2026'); }
        if (text) text.textContent = msg || 'Launching Pwn Machine\u2026';
    }

    function clearTargetLoading() {
        var btn = document.getElementById('instance-btn-launch');
        if (btn) { btn.disabled = false; btn.innerHTML = 'Launch'; }
    }

    function clearPwnLoading() {
        var btn = document.getElementById('pwn-btn-launch');
        if (btn) { btn.disabled = false; btn.innerHTML = 'Launch'; }
    }

    function copyTargetUrl() {
        if (!targetUrl) return;
        var done = function () {
            _showToast({
                color: '#10b981', bg: 'rgba(16,185,129,.08)', border: 'rgba(16,185,129,.3)',
                icon: 'ðŸ“‹', title: 'Copied',
                body: 'vBank URL copied. Paste it in a new tab if Open is blocked.',
                duration: 4000,
            });
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(targetUrl).then(done).catch(function () {
                window.prompt('Copy this URL', targetUrl);
            });
        } else {
            window.prompt('Copy this URL', targetUrl);
        }
    }

    function initMissionHud() {
        if (document.getElementById('ctf-mission-hud')) return;
        var hud = document.createElement('div');
        hud.id = 'ctf-mission-hud';
        hud.innerHTML =
            '<div class="ctf-hud-head">' +
              '<div class="ctf-hud-brand">' +
                '<h1>Challenges</h1>' +
                '<div class="ctf-hud-progress">' +
                  '<div class="ctf-hud-bar"><i id="ctf-hud-bar"></i></div>' +
                  '<span id="ctf-hud-solved">â€”</span>' +
                '</div>' +
              '</div>' +
              '<div class="ctf-hud-metrics">' +
                '<div class="ctf-hud-metric"><strong id="ctf-hud-score">â€”</strong><span>Score</span></div>' +
                '<div class="ctf-hud-metric"><strong id="ctf-hud-place">â€”</strong><span>Rank</span></div>' +
              '</div>' +
              '<label class="ctf-hud-search">' +
                '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3-3"/></svg>' +
                '<input id="ctf-hud-q" type="search" placeholder="Search challenges" autocomplete="off">' +
                '<kbd>/</kbd>' +
              '</label>' +
            '</div>' +
            '<div class="ctf-hud-toolbar">' +
              '<div class="ctf-hud-seg" id="ctf-hud-filters" role="tablist">' +
                '<button type="button" class="is-on" data-filter="all">All</button>' +
                '<button type="button" data-filter="unsolved">Unsolved</button>' +
                '<button type="button" data-filter="solved">Solved</button>' +
              '</div>' +
              '<div class="ctf-hud-cats" id="ctf-hud-cats"></div>' +
            '</div>' +
            '<p class="ctf-hud-empty" id="ctf-hud-empty" hidden>No challenges match.</p>';
        var banner = document.getElementById('target-instance-banner');
        var main = document.querySelector('main') || document.body;
        if (banner && banner.parentNode) banner.insertAdjacentElement('beforebegin', hud);
        else main.insertAdjacentElement('afterbegin', hud);

        var state = { filter: 'all', cat: '' };
        function cards() {
            return Array.prototype.slice.call(document.querySelectorAll('.challenge-button'));
        }
        function wrapFor(btn) {
            return btn.closest('.col-md-3, .col-lg-3, .col-md-4, .col-sm-6, [class*="col-"]') || btn.parentElement || btn;
        }
        function categoryFor(btn) {
            var row = btn.closest('.row');
            var prev = row ? row.previousElementSibling : null;
            while (prev) {
                if (prev.classList && prev.classList.contains('category-header')) {
                    return (prev.textContent || '').trim();
                }
                var inner = prev.querySelector && prev.querySelector('.category-header, h3, h2');
                if (prev.matches && (prev.matches('h2, h3') || inner)) {
                    return ((inner && inner.textContent) || prev.textContent || '').trim();
                }
                prev = prev.previousElementSibling;
            }
            return '';
        }
        function isSolved(btn) {
            return btn.classList.contains('challenge-solved') || btn.classList.contains('solved-challenge');
        }
        function apply() {
            var q = ((document.getElementById('ctf-hud-q') || {}).value || '').toLowerCase().trim();
            var visible = 0;
            cards().forEach(function (btn) {
                var name = ((btn.querySelector('.challenge-name, p') || btn).textContent || '').toLowerCase();
                var solved = isSolved(btn);
                var cat = categoryFor(btn);
                var ok = true;
                if (q && name.indexOf(q) === -1) ok = false;
                if (state.filter === 'solved' && !solved) ok = false;
                if (state.filter === 'unsolved' && solved) ok = false;
                if (state.cat && cat.toLowerCase() !== state.cat.toLowerCase()) ok = false;
                wrapFor(btn).style.display = ok ? '' : 'none';
                if (ok) visible += 1;
            });
            document.querySelectorAll('.category-header, .chal-category').forEach(function (h) {
                var row = h.nextElementSibling;
                var any = false;
                if (row) {
                    row.querySelectorAll('.challenge-button').forEach(function (b) {
                        if (wrapFor(b).style.display !== 'none') any = true;
                    });
                }
                h.style.display = any ? '' : 'none';
                if (row && row.classList.contains('row')) row.style.display = any ? '' : 'none';
            });
            var empty = document.getElementById('ctf-hud-empty');
            if (empty) empty.hidden = visible !== 0 || cards().length === 0;
            var solvedEl = document.getElementById('ctf-hud-solved');
            var bar = document.getElementById('ctf-hud-bar');
            if (solvedEl) {
                var all = cards();
                var n = all.filter(isSolved).length;
                solvedEl.textContent = all.length ? (n + ' / ' + all.length + ' solved') : 'â€”';
                if (bar) bar.style.width = all.length ? Math.round(n / all.length * 100) + '%' : '0%';
            }
        }
        function buildCats() {
            var box = document.getElementById('ctf-hud-cats');
            if (!box) return;
            var names = [];
            document.querySelectorAll('.category-header').forEach(function (h) {
                var name = (h.textContent || '').trim();
                if (name && names.indexOf(name) === -1) names.push(name);
            });
            if (!names.length) {
                box.innerHTML = '';
                return;
            }
            function shortName(name) {
                return name.replace(/^Web\s*[â€”â€“-]\s*/i, '');
            }
            box.innerHTML = names.map(function (name) {
                return '<button type="button" class="ctf-hud-chip" data-cat="' + _esc(name) + '">' + _esc(shortName(name)) + '</button>';
            }).join('');
        }
        function setOn(group, btn) {
            group.querySelectorAll('button').forEach(function (el) { el.classList.remove('is-on'); });
            btn.classList.add('is-on');
        }
        document.getElementById('ctf-hud-filters').addEventListener('click', function (e) {
            var btn = e.target.closest('[data-filter]');
            if (!btn) return;
            state.filter = btn.getAttribute('data-filter') || 'all';
            setOn(document.getElementById('ctf-hud-filters'), btn);
            apply();
        });
        document.getElementById('ctf-hud-cats').addEventListener('click', function (e) {
            var btn = e.target.closest('[data-cat]');
            if (!btn) return;
            if (btn.classList.contains('is-on')) {
                btn.classList.remove('is-on');
                state.cat = '';
            } else {
                state.cat = btn.getAttribute('data-cat') || '';
                setOn(document.getElementById('ctf-hud-cats'), btn);
            }
            apply();
        });
        document.getElementById('ctf-hud-q').addEventListener('input', apply);
        document.addEventListener('keydown', function (e) {
            if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
            var tag = (e.target && e.target.tagName) || '';
            if (tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable)) return;
            e.preventDefault();
            var input = document.getElementById('ctf-hud-q');
            if (input) input.focus();
        });
        fetch('/api/v1/users/me', { credentials: 'same-origin' })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var u = (d && d.data) || {};
                var score = document.getElementById('ctf-hud-score');
                var place = document.getElementById('ctf-hud-place');
                if (score) {
                    score.textContent = u.score != null ? u.score : 'â€”';
                    score.classList.toggle('is-neg', Number(u.score) < 0);
                }
                if (place) {
                    var p = u.place == null || u.place === '' ? '' : String(u.place);
                    place.textContent = !p ? 'â€”' : (/^\d+$/.test(p) ? '#' + p : p.replace(/^#/, ''));
                }
            })
            .catch(function () {});
        var tries = 0;
        function ready() {
            tries += 1;
            if (cards().length || tries > 20) {
                buildCats();
                apply();
                return;
            }
            setTimeout(ready, 400);
        }
        ready();
        var board = document.querySelector('#challenges-board, #challenge-window, main');
        if (board && window.MutationObserver) {
            var timer = null;
            var obs = new MutationObserver(function () {
                clearTimeout(timer);
                timer = setTimeout(function () {
                    buildCats();
                    apply();
                }, 250);
            });
            obs.observe(board, { childList: true, subtree: true });
        }
    }

    function launchTarget() {
        setTargetLoading('Spinning up target\u2026');
        apiFetch('/plugins/ctfd-target/target/start', { method: 'POST', body: '{}' })
            .then(function (data) { clearTargetLoading(); updateTargetUI(data); })
            .catch(function () { clearTargetLoading(); });
    }

    function resetTarget() {
        var resetBtn  = document.getElementById('instance-btn-reset');
        var destroyBtn = document.getElementById('instance-btn-destroy');
        var openBtn   = document.getElementById('instance-btn-open');
        var text      = document.getElementById('instance-status-text');
        if (resetBtn)  { resetBtn.disabled = true; resetBtn.innerHTML = '<span class="instance-spinner"></span>Resetting\u2026'; }
        if (destroyBtn) destroyBtn.style.display = 'none';
        if (openBtn)    openBtn.style.display = 'none';
        if (text) text.textContent = 'Destroying old instance\u2026';
        clearInterval(countdownTimer);
        apiFetch('/plugins/ctfd-target/target/stop', { method: 'DELETE' })
            .then(function() {
                if (text) text.textContent = 'Launching fresh instance\u2026';
                return apiFetch('/plugins/ctfd-target/target/start', { method: 'POST', body: '{}' });
            })
            .then(function(data) {
                if (resetBtn) { resetBtn.disabled = false; resetBtn.innerHTML = 'Reset'; }
                _autoExtended = false;
                updateTargetUI(data);
            })
            .catch(function() {
                if (resetBtn) { resetBtn.disabled = false; resetBtn.innerHTML = 'Reset'; }
                refreshStatus();
            });
    }

    function destroyTarget() {
        var btn = document.getElementById('instance-btn-destroy');
        var text = document.getElementById('instance-status-text');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner instance-spinner-red"></span>Stopping\u2026'; }
        if (text) text.textContent = 'Stopping\u2026';
        apiFetch('/plugins/ctfd-target/target/stop', { method: 'DELETE' })
            .then(function () {
                clearInterval(countdownTimer);
                updateTargetUI({ success: true, status: 'not_found' });
            })
            .catch(function () {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Stop'; }
            });
    }

    function launchPwn() {
        clearInterval(_pwnPollingTimer);
        _pwnPollingCount = 0;
        setPwnLoading('Starting Kali machine\u2026');
        apiFetch('/plugins/ctfd-target/kali/start', { method: 'POST', body: '{}' })
            .then(function (data) {
                if (data && data.success) {
                    startPwnPolling();
                } else {
                    clearPwnLoading();
                    updatePwnUI(data);
                }
            })
            .catch(function () { clearPwnLoading(); });
    }

    function startPwnPolling() {
        _pwnPollingCount = 0;
        pollPwnReady();
    }

    function pollPwnReady() {
        _pwnPollingCount++;
        var text = document.getElementById('pwn-status-text');
        var btn  = document.getElementById('pwn-btn-launch');
        var dots = Array(_pwnPollingCount % 4 + 1).join('.') || '.';
        if (btn)  { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner"></span>Kali is loading' + dots; }
        if (text) text.textContent = 'Kali is loading\u2026 (' + _pwnPollingCount + '/' + _pwnPollingMax + ')';

        apiFetch('/plugins/ctfd-target/kali/status', { method: 'GET' })
            .then(function (data) {
                if (data && data.success && data.status === 'running' && data.url) {
                    clearPwnLoading();
                    updatePwnUI(data);
                } else if (_pwnPollingCount < _pwnPollingMax) {
                    _pwnPollingTimer = setTimeout(pollPwnReady, 2000);
                } else {
                    clearPwnLoading();
                    // Still show what we have \u2014 container may be up but URL not bound yet
                    if (data && data.success && data.status === 'running') {
                        updatePwnUI(data);
                    } else {
                        var text2 = document.getElementById('pwn-status-text');
                        if (text2) text2.textContent = 'Kali is taking longer than expected \u2014 try refreshing the page.';
                    }
                }
            })
            .catch(function () {
                if (_pwnPollingCount < _pwnPollingMax) {
                    _pwnPollingTimer = setTimeout(pollPwnReady, 2000);
                } else {
                    clearPwnLoading();
                }
            });
    }

    function extendPwn() {
        var btn  = document.getElementById('pwn-btn-extend');
        var text = document.getElementById('pwn-status-text');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner"></span>Extending\u2026'; }
        apiFetch('/plugins/ctfd-target/kali/extend', { method: 'POST', body: '{}' })
            .then(function (data) {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Extend 1 hour'; }
                if (data && data.success) {
                    showExpiryToast('Kali machine extended +1 hour.', false);
                    setTimeout(refreshStatus, 1000);
                } else if (data && data.limit_reached) {
                    if (text) text.textContent = 'Session limit (70 min) reached \u2014 cannot extend further.';
                    if (btn)  btn.style.display = 'none';
                    showExpiryToast('70-minute session limit reached \u2014 Kali cannot be extended.', true);
                } else {
                    showExpiryToast((data && data.msg) || 'Could not extend Kali machine.', true);
                }
            })
            .catch(function () {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Extend 1 hour'; }
            });
    }

    function openPwnSSO() {
        apiFetch('/plugins/ctfd-target/kali/sso-url', { method: 'GET' })
            .then(function (data) {
                if (data && data.success && data.url) {
                    window.open(data.url, '_blank');
                } else {
                    if (pwnUrl) window.open(pwnUrl, '_blank');
                }
            })
            .catch(function () {
                if (pwnUrl) window.open(pwnUrl, '_blank');
            });
    }

    function destroyPwn() {
        var btn = document.getElementById('pwn-btn-destroy');
        var text = document.getElementById('pwn-status-text');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner instance-spinner-red"></span>Stopping\u2026'; }
        if (text) text.textContent = 'Stopping\u2026';
        apiFetch('/plugins/ctfd-target/kali/stop', { method: 'DELETE' })
            .then(function () {
                clearInterval(pwnCountdownTimer);
                updatePwnUI({ success: true, status: 'not_found' });
            })
            .catch(function () {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Stop'; }
            });
    }

    function updateTargetUI(data) {
        var text = document.getElementById('instance-status-text');
        var urlRow = document.getElementById('instance-url-row');
        var urlLink = document.getElementById('instance-url-link');
        var openBtn = document.getElementById('instance-btn-open');
        var destroyBtn = document.getElementById('instance-btn-destroy');
        var launchBtn = document.getElementById('instance-btn-launch');
        var resetBtn = document.getElementById('instance-btn-reset');
        var timerWrap = document.getElementById('instance-timer-wrap');
        var dot = document.getElementById('target-dot');
        var panel = document.querySelector('.instance-panel-target');

        var targetRunning = data && data.success && (data.status === 'running' || data.status === 'paused' || data.status === 'deployed');
        if (targetRunning) {
            targetUrl = data.url;
            expiresAt = data.expires_at;
            text.textContent = data.worker ? ('Running on ' + data.worker) : 'Running';
            if (dot) { dot.style.display = 'inline-block'; }
            if (panel) { panel.classList.add('panel-active'); }
            urlRow.style.display = 'flex';
            urlLink.href = data.url;
            urlLink.textContent = 'Open vBank';
            openBtn.style.display = '';
            destroyBtn.style.display = '';
            if (resetBtn) resetBtn.style.display = '';
            var copyBtn = document.getElementById('instance-btn-copy');
            if (copyBtn) copyBtn.style.display = targetUrl ? '' : 'none';
            launchBtn.style.display = 'none';
            timerWrap.style.display = '';
            startCountdown('instance-timer', expiresAt, false);
            return;
        }

        targetUrl = null;
        if (dot) { dot.style.display = 'none'; }
        if (panel) { panel.classList.remove('panel-active'); }
        urlRow.style.display = 'none';
        openBtn.style.display = 'none';
        destroyBtn.style.display = 'none';
        var copyBtnOff = document.getElementById('instance-btn-copy');
        if (copyBtnOff) copyBtnOff.style.display = 'none';
        if (resetBtn) resetBtn.style.display = 'none';
        launchBtn.style.display = '';
        timerWrap.style.display = 'none';
        text.textContent = (data && data.msg) ? data.msg : 'Not running';
        clearInterval(countdownTimer);
    }

    function updatePwnUI(data) {
        var text = document.getElementById('pwn-status-text');
        var urlRow = document.getElementById('pwn-url-row');
        var urlLink = document.getElementById('pwn-url-link');
        var openBtn = document.getElementById('pwn-btn-open');
        var extendBtn = document.getElementById('pwn-btn-extend');
        var destroyBtn = document.getElementById('pwn-btn-destroy');
        var launchBtn = document.getElementById('pwn-btn-launch');
        var timerWrap = document.getElementById('pwn-timer-wrap');
        var dot = document.getElementById('pwn-dot');
        var panel = document.querySelector('.instance-panel-pwn');

        if (data && data.success && (data.status === 'running' || data.status === 'paused')) {
            pwnExpiresAt = data.expires_at;
            if (data.created_at) _pwnCreatedAt = data.created_at;
            destroyBtn.style.display = '';
            launchBtn.style.display = 'none';
            timerWrap.style.display = '';
            if (panel) { panel.classList.add('panel-active'); }
            startCountdown('pwn-timer', pwnExpiresAt, true);

            // Show Extend button only if under the 70-min session cap
            var sessionAge = _pwnCreatedAt ? (Math.floor(Date.now() / 1000) - _pwnCreatedAt) : 0;
            if (extendBtn) extendBtn.style.display = sessionAge < KALI_MAX_LIFETIME ? '' : 'none';

            if (data.url) {
                pwnUrl = data.url;
                text.textContent = data.worker ? ('Running on ' + data.worker) : 'Running';
                if (dot) { dot.style.display = 'inline-block'; }
                urlRow.style.display = 'flex';
                urlLink.href = data.url;
                urlLink.textContent = 'Open';
                openBtn.style.display = '';
            } else {
                pwnUrl = null;
                text.textContent = 'Kali is loading â€” allocating portâ€¦';
                if (dot) { dot.style.display = 'none'; }
                urlRow.style.display = 'none';
                openBtn.style.display = 'none';
                if (!_pwnPollingTimer && _pwnPollingCount < _pwnPollingMax) {
                    _pwnPollingTimer = setTimeout(function () {
                        _pwnPollingTimer = null;
                        pollPwnReady();
                    }, 2000);
                }
            }
            return;
        }

        pwnUrl = null;
        _pwnCreatedAt = null;
        if (dot) { dot.style.display = 'none'; }
        if (panel) { panel.classList.remove('panel-active'); }
        urlRow.style.display = 'none';
        openBtn.style.display = 'none';
        if (extendBtn) extendBtn.style.display = 'none';
        destroyBtn.style.display = 'none';
        launchBtn.style.display = '';
        timerWrap.style.display = 'none';
        text.textContent = (data && data.msg) ? data.msg : 'Not running';
        clearInterval(pwnCountdownTimer);
        clearTimeout(_pwnPollingTimer);
        _pwnPollingTimer = null;
    }

    function showExpiryToast(msg, urgent) {
        _showToast({
            color: urgent ? '#f87171' : '#fbbf24',
            bg: urgent ? 'rgba(248,113,113,.10)' : 'rgba(251,191,36,.08)',
            border: urgent ? 'rgba(248,113,113,.35)' : 'rgba(251,191,36,.30)',
            icon: urgent ? 'ðŸš¨' : 'âš ï¸',
            title: urgent ? 'Expiring soon' : 'Instance warning',
            body: msg,
            duration: 8000,
        });
    }

    function startCountdown(elementId, ts, isKali) {
        var warned = isKali ? _warnedPwn : _warnedTarget;

        function tick() {
            var remaining = Math.max(0, ts - Math.floor(Date.now() / 1000));
            var el = document.getElementById(elementId);
            if (!el) return;
            var mins = Math.floor(remaining / 60);
            var secs = remaining % 60;
            el.textContent = (mins < 10 ? '0' : '') + mins + ':' + (secs < 10 ? '0' : '') + secs;

            var label = isKali ? 'Pwn Machine' : 'Target';

            // Auto-extend at 15 minutes â€” target countdown extends both target+kali
            if (!isKali && remaining <= 900 && remaining > 895 && !_autoExtended) {
                _autoExtended = true;
                fetch('/plugins/ctfd-target/target/extend', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'CSRF-Token': getCsrfToken() }
                }).then(function(r) { return r.json(); })
                  .then(function(d) {
                    if (d.success) {
                        showExpiryToast('Auto-extended +1h â€” you were still active.', false);
                        setTimeout(refreshStatus, 1500);
                    }
                  }).catch(function(){});
            }

            // Auto-extend PWN at 15 minutes â€” only if under 70-min session cap
            if (isKali && remaining <= 900 && remaining > 895 && !_autoExtendedPwn) {
                var pwnAge = _pwnCreatedAt ? (Math.floor(Date.now() / 1000) - _pwnCreatedAt) : 0;
                if (pwnAge < KALI_MAX_LIFETIME) {
                    _autoExtendedPwn = true;
                    fetch('/plugins/ctfd-target/kali/extend', {
                        method: 'POST',
                        credentials: 'same-origin',
                        headers: { 'Content-Type': 'application/json', 'CSRF-Token': getCsrfToken() }
                    }).then(function(r) { return r.json(); })
                      .then(function(d) {
                        if (d.success) {
                            showExpiryToast('Kali auto-extended +1h â€” you were still active.', false);
                            setTimeout(refreshStatus, 1500);
                        } else if (d.limit_reached) {
                            var extBtn = document.getElementById('pwn-btn-extend');
                            if (extBtn) extBtn.style.display = 'none';
                        }
                      }).catch(function(){});
                }
            }

            if (remaining <= 300 && remaining > 295 && !warned['5min']) {
                warned['5min'] = true;
                showExpiryToast(label + ' auto-destroys in 5 minutes.', false);
            }
            if (remaining <= 60 && remaining > 55 && !warned['1min']) {
                warned['1min'] = true;
                showExpiryToast(label + ' auto-destroys in 1 minute!', true);
            }

            if (remaining === 0) {
                warned['5min'] = false;
                warned['1min'] = false;
                if (isKali) {
                    _autoExtendedPwn = false;
                    updatePwnUI({ success: true, status: 'not_found' });
                } else {
                    _autoExtended = false;
                    updateTargetUI({ success: true, status: 'not_found' });
                }
            }
        }

        if (isKali) {
            _warnedPwn = {};
            _autoExtendedPwn = false;
        } else {
            _warnedTarget = {};
            _autoExtended = false;
        }

        tick();
        if (isKali) {
            clearInterval(pwnCountdownTimer);
            pwnCountdownTimer = setInterval(tick, 1000);
        } else {
            clearInterval(countdownTimer);
            countdownTimer = setInterval(tick, 1000);
        }
    }

    // â”€â”€ Per-challenge instance panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    var _chalEnabled = {};
    var _chalCurrentId = null;
    var _chalUrl = null;
    var _chalTimer = null;
    var _chalPoll = null;
    var _chalWarned = {};
    var _chalAutoExtended = false;

    function initPerChallenge() {
        document.body.classList.add('ctf-challenges-page');
        initMissionHud();
        apiFetch('/plugins/ctfd-target/challenge-target/configs', { method: 'GET' })
            .then(function (data) {
                (data.ids || []).forEach(function (id) { _chalEnabled[id] = true; _chalEnabled[String(id)] = true; });
                setInterval(_watchChallengeModal, 500);
                window.addEventListener('hashchange', _watchChallengeModal);
                document.addEventListener('shown.bs.modal', _watchChallengeModal);
                document.addEventListener('click', function (ev) {
                    var btn = ev.target.closest && ev.target.closest('[data-challenge-id], .challenge-button, .challenge-card');
                    if (btn) setTimeout(_watchChallengeModal, 250);
                });
                _watchChallengeModal();
            })
            .catch(function () {});
    }

    function _findOpenChallengeId() {
        var hidden = document.querySelector('#challenge-window #challenge-id, .challenge-window #challenge-id, #challenge-id');
        if (hidden && hidden.textContent && hidden.textContent.trim()) {
            var hid = parseInt(hidden.textContent.trim(), 10);
            if (hid) return hid;
        }
        var input = document.querySelector('#challenge-window input[name="challenge_id"], .modal.show input[name="challenge_id"]');
        if (input && input.value) {
            var iid = parseInt(input.value, 10);
            if (iid) return iid;
        }
        try {
            if (window.Alpine) {
                var el = document.querySelector('#challenge-window, [x-ref="challengeWindow"]');
                if (el) {
                    var data = window.Alpine.$data(el);
                    if (data && data.id) return parseInt(data.id, 10);
                }
            }
        } catch (e) {}
        var modal = document.querySelector('#challenge-window.show, #challenge-window.modal.show, .modal.show');
        if (modal) {
            var attr = modal.getAttribute('data-challenge-id');
            if (attr) return parseInt(attr, 10);
        }
        var hash = (location.hash || '').match(/(\d+)/);
        if (hash) return parseInt(hash[1], 10);
        return null;
    }

    function _challengeModalOpen() {
        var modal = document.querySelector('#challenge-window, [x-ref="challengeWindow"], .challenge-window');
        if (!modal) return false;
        if (modal.classList.contains('show') || modal.style.display === 'block') return true;
        if (modal.getAttribute('aria-hidden') === 'false') return true;
        try {
            if (window.Alpine) {
                var data = window.Alpine.$data(modal);
                if (data && (data.id || data.open || data.show)) return true;
            }
        } catch (e) {}
        return !!(document.querySelector('.modal.show, .modal[style*="display: block"]'));
    }

    function _watchChallengeModal() {
        var open = _challengeModalOpen();
        var id = open ? _findOpenChallengeId() : null;
        if (!id || !(_chalEnabled[id] || _chalEnabled[String(id)])) {
            if (!open) {
                _chalCurrentId = null;
                if (_chalPoll) { clearInterval(_chalPoll); _chalPoll = null; }
                if (_chalTimer) { clearInterval(_chalTimer); _chalTimer = null; }
            }
            return;
        }
        if (_chalCurrentId !== id) {
            var oldPanel = document.getElementById('chal-instance-panel');
            if (oldPanel) oldPanel.remove();
            _chalCurrentId = id;
            _chalUrl = null;
            _chalWarned = {};
            _chalAutoExtended = false;
            _injectChalPanel(id);
            _refreshChalStatus(id);
            if (_chalPoll) clearInterval(_chalPoll);
            _chalPoll = setInterval(function () { _refreshChalStatus(id); }, POLL_INTERVAL);
        } else if (!document.getElementById('chal-instance-panel')) {
            _injectChalPanel(id);
            _refreshChalStatus(id);
        }
    }

    function _injectChalPanel(challengeId) {
        if (document.getElementById('chal-instance-panel')) {
            document.getElementById('chal-instance-panel').setAttribute('data-challenge-id', String(challengeId));
            return;
        }
        var host = document.querySelector('#challenge-window .modal-body, #challenge-window .challenge-desc, [x-ref="challengeWindow"] .modal-body, .challenge-window .modal-body, #challenge-window');
        if (!host) return;
        var panel = document.createElement('div');
        panel.id = 'chal-instance-panel';
        panel.setAttribute('data-challenge-id', String(challengeId));
        panel.innerHTML =
            '<div class="chal-instance-inner">' +
            '  <div class="chal-instance-head">' +
            '    <span class="chal-instance-title">Target instance</span>' +
            '    <span class="instance-status-dot" id="chal-dot"></span>' +
            '    <span class="chal-instance-status" id="chal-status-text">No instance running</span>' +
            '  </div>' +
            '  <div class="chal-instance-url" id="chal-url-row" style="display:none;">' +
            '    <a id="chal-url-link" href="#" target="_blank" rel="noopener">Open target</a>' +
            '  </div>' +
            '  <div class="chal-instance-timer" id="chal-timer-wrap" style="display:none;">Auto-destroys in <strong id="chal-timer">60:00</strong></div>' +
            '  <div class="chal-instance-actions">' +
            '    <button type="button" class="instance-btn instance-btn-launch" id="chal-btn-launch">Launch Instance</button>' +
            '    <button type="button" class="instance-btn instance-btn-open" id="chal-btn-open" style="display:none;">Open</button>' +
            '    <button type="button" class="instance-btn instance-btn-extend" id="chal-btn-extend" style="display:none;">Extend 1 hour</button>' +
            '    <button type="button" class="instance-btn instance-btn-destroy" id="chal-btn-destroy" style="display:none;">Stop</button>' +
            '  </div>' +
            '  <div class="chal-instance-msg" id="chal-msg"></div>' +
            '</div>';
        host.insertBefore(panel, host.firstChild);
        document.getElementById('chal-btn-launch').addEventListener('click', function () { _chalLaunch(challengeId); });
        document.getElementById('chal-btn-open').addEventListener('click', function () { if (_chalUrl) window.open(_chalUrl, '_blank'); });
        document.getElementById('chal-btn-destroy').addEventListener('click', function () { _chalStop(challengeId); });
        document.getElementById('chal-btn-extend').addEventListener('click', function () { _chalExtend(challengeId); });
    }

    function _chalApi(path, method, challengeId) {
        var url = '/plugins/ctfd-target/challenge-target/' + path;
        var opts = { method: method, body: '{}' };
        if (method === 'GET' || method === 'DELETE') {
            url += '?challenge_id=' + encodeURIComponent(challengeId);
            opts = { method: method };
        } else {
            opts.body = JSON.stringify({ challenge_id: challengeId });
        }
        return apiFetch(url, opts);
    }

    function _chalLaunch(challengeId) {
        var btn = document.getElementById('chal-btn-launch');
        var text = document.getElementById('chal-status-text');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner"></span>Launchingâ€¦'; }
        if (text) text.textContent = 'Spinning up instanceâ€¦';
        _chalApi('start', 'POST', challengeId)
            .then(function (data) {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Launch Instance'; }
                _updateChalUI(data);
                if (data && !data.success && data.msg) {
                    var msg = document.getElementById('chal-msg');
                    if (msg) msg.textContent = data.msg;
                }
            })
            .catch(function () {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Launch Instance'; }
            });
    }

    function _chalStop(challengeId) {
        var btn = document.getElementById('chal-btn-destroy');
        if (btn) { btn.disabled = true; btn.innerHTML = '<span class="instance-spinner instance-spinner-red"></span>Destroyingâ€¦'; }
        _chalApi('stop', 'DELETE', challengeId)
            .then(function () {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Stop'; }
                _updateChalUI({ success: true, status: 'not_found', challenge_id: challengeId });
            })
            .catch(function () {
                if (btn) { btn.disabled = false; btn.innerHTML = 'Stop'; }
            });
    }

    function _chalExtend(challengeId) {
        _chalApi('extend', 'POST', challengeId).then(function (data) {
            if (data && data.success) _refreshChalStatus(challengeId);
            var msg = document.getElementById('chal-msg');
            if (msg) msg.textContent = (data && data.msg) || '';
        });
    }

    function _refreshChalStatus(challengeId) {
        if (_chalCurrentId !== challengeId) return;
        _chalApi('status', 'GET', challengeId).then(_updateChalUI).catch(function () {});
    }

    function _updateChalUI(data) {
        if (!data || !document.getElementById('chal-instance-panel')) return;
        var running = data.success && data.status === 'running' && data.url;
        var launch = document.getElementById('chal-btn-launch');
        var open = document.getElementById('chal-btn-open');
        var destroy = document.getElementById('chal-btn-destroy');
        var extend = document.getElementById('chal-btn-extend');
        var text = document.getElementById('chal-status-text');
        var urlRow = document.getElementById('chal-url-row');
        var urlLink = document.getElementById('chal-url-link');
        var timerWrap = document.getElementById('chal-timer-wrap');
        var dot = document.getElementById('chal-dot');
        var msg = document.getElementById('chal-msg');
        if (msg && data.msg && !data.success) msg.textContent = data.msg;
        else if (msg && data.success) msg.textContent = '';

        if (running) {
            _chalUrl = data.url;
            if (launch) launch.style.display = 'none';
            if (open) open.style.display = '';
            if (destroy) destroy.style.display = '';
            if (extend) extend.style.display = '';
            if (text) text.textContent = 'Instance running';
            if (urlRow) urlRow.style.display = '';
            if (urlLink) { urlLink.href = data.url; urlLink.textContent = data.url; }
            if (dot) dot.classList.add('dot-on');
            if (timerWrap) timerWrap.style.display = '';
            if (data.expires_at) _startChalCountdown(data.expires_at, data.challenge_id || _chalCurrentId);
        } else {
            _chalUrl = null;
            if (launch) launch.style.display = '';
            if (open) open.style.display = 'none';
            if (destroy) destroy.style.display = 'none';
            if (extend) extend.style.display = 'none';
            if (text) text.textContent = 'No instance running';
            if (urlRow) urlRow.style.display = 'none';
            if (dot) dot.classList.remove('dot-on');
            if (timerWrap) timerWrap.style.display = 'none';
            if (_chalTimer) { clearInterval(_chalTimer); _chalTimer = null; }
        }
    }

    function _startChalCountdown(expiresAt, challengeId) {
        if (_chalTimer) clearInterval(_chalTimer);
        function tick() {
            var remaining = Math.max(0, Math.floor(expiresAt - (Date.now() / 1000)));
            var el = document.getElementById('chal-timer');
            if (el) {
                var m = Math.floor(remaining / 60);
                var s = remaining % 60;
                el.textContent = m + ':' + (s < 10 ? '0' : '') + s;
            }
            if (remaining <= 900 && remaining > 890 && !_chalAutoExtended) {
                _chalAutoExtended = true;
                _chalExtend(challengeId);
            }
            if (remaining === 0) {
                _updateChalUI({ success: true, status: 'not_found' });
            }
        }
        tick();
        _chalTimer = setInterval(tick, 1000);
    }
})();


