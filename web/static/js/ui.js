/* ===========================================================================
   Vignette — UI primitives
   ---------------------------------------------------------------------------
   Replaces the Bootstrap bundle that used to be pulled from a CDN. Only the
   handful of behaviours this interface actually uses are implemented: toasts,
   dialogs, tabs, accordions, a confirm dialog and the theme switch.

   Everything here is dependency-free and safe to run before the DOM is ready.
   =========================================================================== */

(function (window, document) {
    'use strict';

    var V = window.V = window.V || {};

    /* ── i18n ───────────────────────────────────────────────────────────── */

    // Populated by base.html as a JSON blob (not string-interpolated into JS,
    // so quotes or apostrophes in a translation can never break the page).
    V.i18n = window.VIGNETTE_I18N || {};
    V.t = function (key, fallback) {
        var val = V.i18n[key];
        return (val === undefined || val === null || val === '') ? (fallback || key) : val;
    };

    /* ── Small helpers ──────────────────────────────────────────────────── */

    function $(sel, root) { return (root || document).querySelector(sel); }
    function $$(sel, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(sel));
    }
    V.$ = $;
    V.$$ = $$;

    // Kept in step with templates/_icons.html — these are the ones the
    // script-built parts of the interface draw (toasts, system rows, tiles).
    var ICONS = {
        'check-circle': '<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.8 2.8L16 9.5"/>',
        'alert':        '<path d="M10.3 3.6 1.9 18a2 2 0 0 0 1.7 3h16.8a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0Z"/><path d="M12 9.5v4"/><path d="M12 17.4h.01"/>',
        'info':         '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
        'x':            '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
        'server':       '<rect x="3" y="3.5" width="18" height="7" rx="1.8"/><rect x="3" y="13.5" width="18" height="7" rx="1.8"/><path d="M7 7h.01"/><path d="M7 17h.01"/>',
        'wifi':         '<path d="M5 12.55a11 11 0 0 1 14 0"/><path d="M1.4 9a16 16 0 0 1 21.2 0"/><path d="M8.5 16.1a6 6 0 0 1 7 0"/><path d="M12 20h.01"/>',
        'thermometer':  '<path d="M14 14.8V4.5a2.5 2.5 0 0 0-5 0v10.3a4.5 4.5 0 1 0 5 0Z"/>',
        'memory':       '<rect x="2.5" y="7" width="19" height="10" rx="1.8"/><path d="M6.5 11v2.5"/><path d="M10 11v2.5"/><path d="M13.5 11v2.5"/><path d="M17 11v2.5"/>',
        'disk':         '<ellipse cx="12" cy="6" rx="8.5" ry="3.2"/><path d="M3.5 6v12c0 1.77 3.8 3.2 8.5 3.2s8.5-1.43 8.5-3.2V6"/><path d="M3.5 12c0 1.77 3.8 3.2 8.5 3.2s8.5-1.43 8.5-3.2"/>',
        'clock':        '<circle cx="12" cy="12" r="9"/><path d="M12 7v5.3l3.4 2"/>',
        'gallery':      '<rect x="3" y="3" width="18" height="18" rx="2.5"/><circle cx="8.6" cy="8.6" r="1.6"/><path d="m3.5 17 4.6-4.4a2 2 0 0 1 2.7 0L20.5 21"/>',
        /* Same path as the server-side icon of this name. A name missing from
           here silently draws the info circle instead, which is how a remove
           button came to be marked with an "i". */
        'trash':        '<path d="M3.5 6h17"/><path d="M8.5 6V4.5A1.5 1.5 0 0 1 10 3h4a1.5 1.5 0 0 1 1.5 1.5V6"/><path d="M18.5 6v13a2 2 0 0 1-2 2h-9a2 2 0 0 1-2-2V6"/><path d="M10 11v5"/><path d="M14 11v5"/>'
    };

    function svg(name, cls) {
        var el = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        el.setAttribute('class', 'icon ' + (cls || ''));
        el.setAttribute('viewBox', '0 0 24 24');
        el.setAttribute('fill', 'none');
        el.setAttribute('stroke', 'currentColor');
        el.setAttribute('stroke-width', '1.75');
        el.setAttribute('stroke-linecap', 'round');
        el.setAttribute('stroke-linejoin', 'round');
        el.setAttribute('aria-hidden', 'true');
        el.innerHTML = ICONS[name] || ICONS.info;
        return el;
    }
    V.svg = svg;

    /* ── Toasts ─────────────────────────────────────────────────────────── */

    var TOAST_ICON = {
        success: 'check-circle',
        danger: 'alert',
        warning: 'alert',
        info: 'info'
    };

    /**
     * Show a transient message.
     * The message is inserted as text, never as HTML — server error strings and
     * filenames reach this function verbatim and must not be able to inject
     * markup.
     */
    V.toast = function (message, type, opts) {
        type = type || 'info';
        opts = opts || {};

        var host = $('#toasts');
        if (!host) {
            host = document.createElement('div');
            host.id = 'toasts';
            host.className = 'toasts';
            host.setAttribute('role', 'status');
            host.setAttribute('aria-live', 'polite');
            document.body.appendChild(host);
        }

        var el = document.createElement('div');
        el.className = 'toast toast--' + type;

        el.appendChild(svg(TOAST_ICON[type] || 'info'));

        var msg = document.createElement('div');
        msg.className = 'toast__msg';
        /* Bounded here, once, because a toast takes whatever it is handed:
           a device error, a JSON parse failure, a value off the query string.
           A toast is a sentence — anything longer has stopped being a message
           and started being a wall, and it covers the page it appeared over. */
        var body = String(message == null ? '' : message);
        msg.textContent = body.length > 200 ? body.slice(0, 197) + '…' : body;
        el.appendChild(msg);

        var close = document.createElement('button');
        close.type = 'button';
        close.className = 'toast__close';
        close.setAttribute('aria-label', V.t('close', 'Close'));
        close.appendChild(svg('x'));
        el.appendChild(close);

        function dismiss() {
            if (el.classList.contains('is-leaving')) return;
            el.classList.add('is-leaving');
            setTimeout(function () { el.remove(); }, 220);
        }
        close.addEventListener('click', dismiss);

        host.appendChild(el);
        var delay = opts.duration || (type === 'danger' ? 6500 : 3800);
        if (delay > 0) setTimeout(dismiss, delay);

        return { dismiss: dismiss };
    };

    /* ── Dialogs ────────────────────────────────────────────────────────── */

    var openStack = [];

    function lockScroll(lock) {
        document.body.classList.toggle('is-locked', lock);
    }

    V.openModal = function (idOrEl) {
        var el = typeof idOrEl === 'string' ? document.getElementById(idOrEl) : idOrEl;
        if (!el || openStack.indexOf(el) !== -1) return;

        el.hidden = false;
        el.setAttribute('aria-hidden', 'false');
        openStack.push(el);
        lockScroll(true);

        var focusable = el.querySelector(
            '[autofocus], .modal__panel button, .modal__panel [href], .modal__panel input, ' +
            '.modal__panel select, .modal__panel textarea'
        );
        if (focusable) {
            try { focusable.focus({ preventScroll: true }); } catch (e) { focusable.focus(); }
        }
    };

    V.closeModal = function (idOrEl) {
        var el = typeof idOrEl === 'string' ? document.getElementById(idOrEl) : idOrEl;
        if (!el) return;
        el.hidden = true;
        el.setAttribute('aria-hidden', 'true');
        openStack = openStack.filter(function (m) { return m !== el; });
        if (!openStack.length) lockScroll(false);
        el.dispatchEvent(new CustomEvent('modal:close'));
    };

    /**
     * Styled replacement for window.confirm().
     * Returns a promise resolving to true/false, so callers read the same as
     * the native API but the dialog matches the rest of the interface and
     * cannot be suppressed by the browser's "prevent additional dialogs" box.
     */
    V.confirm = function (message, opts) {
        opts = opts || {};
        return new Promise(function (resolve) {
            var modal = document.createElement('div');
            modal.className = 'modal';
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');

            var backdrop = document.createElement('div');
            backdrop.className = 'modal__backdrop';

            var panel = document.createElement('div');
            panel.className = 'modal__panel modal__panel--sm';

            var body = document.createElement('div');
            body.className = 'modal__body';

            if (opts.title) {
                var h = document.createElement('h3');
                h.className = 'mb-1';
                h.textContent = opts.title;
                body.appendChild(h);
            }
            var p = document.createElement('p');
            p.className = 'muted mb-0';
            p.textContent = String(message == null ? '' : message);
            body.appendChild(p);

            var foot = document.createElement('div');
            foot.className = 'modal__foot';

            var cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'btn btn--subtle';
            cancel.textContent = opts.cancelText || V.t('cancel', 'Cancel');

            var ok = document.createElement('button');
            ok.type = 'button';
            ok.className = 'btn ' + (opts.danger ? 'btn--danger' : 'btn--primary');
            ok.textContent = opts.confirmText || V.t('confirm', 'Confirm');

            foot.appendChild(cancel);
            foot.appendChild(ok);
            panel.appendChild(body);
            panel.appendChild(foot);
            modal.appendChild(backdrop);
            modal.appendChild(panel);
            document.body.appendChild(modal);

            lockScroll(true);
            openStack.push(modal);
            try { ok.focus({ preventScroll: true }); } catch (e) { ok.focus(); }

            function finish(result) {
                openStack = openStack.filter(function (m) { return m !== modal; });
                if (!openStack.length) lockScroll(false);
                modal.remove();
                resolve(result);
            }
            ok.addEventListener('click', function () { finish(true); });
            cancel.addEventListener('click', function () { finish(false); });
            backdrop.addEventListener('click', function () { finish(false); });
            modal.addEventListener('keydown', function (e) {
                if (e.key === 'Escape') { e.stopPropagation(); finish(false); }
            });
        });
    };

    /* ── Button busy state ──────────────────────────────────────────────── */

    /** Swap a button into a spinner+label state and hand back a restore fn. */
    V.busy = function (btn, label) {
        if (!btn) return function () {};
        var original = btn.innerHTML;
        btn.classList.add('is-busy');
        btn.disabled = true;
        btn.innerHTML = '';
        var spin = document.createElement('span');
        spin.className = 'btn__spinner';
        btn.appendChild(spin);
        if (label) {
            var span = document.createElement('span');
            span.textContent = label;
            btn.appendChild(span);
        }
        return function () {
            btn.classList.remove('is-busy');
            btn.disabled = false;
            btn.innerHTML = original;
        };
    };

    /* ── Theme ──────────────────────────────────────────────────────────── */

    var THEME_KEY = 'vignette-theme';

    /* Dark is the default, and the operating system's mode is not consulted:
       the theme is whatever was last chosen here and nothing else, so the
       console looks the same on every phone that opens it. Matches the
       pre-paint script in templates/_theme_boot.html — change both together. */
    V.applyTheme = function (theme) {
        var resolved = (theme === 'light') ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', resolved);
        document.documentElement.setAttribute('data-theme-pref', theme || 'light');
        document.documentElement.style.colorScheme = resolved;
        var meta = $('meta[name="theme-color"]');
        if (meta) meta.setAttribute('content', resolved === 'dark' ? '#0e1014' : '#f4f2ef');
    };

    V.toggleTheme = function () {
        var current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
        var next = current === 'dark' ? 'light' : 'dark';
        try { localStorage.setItem(THEME_KEY, next); } catch (e) { /* private mode */ }
        V.applyTheme(next);
    };

    /* ── Declarative wiring ─────────────────────────────────────────────── */

    function initTabs(root) {
        $$('[data-tabs]', root).forEach(function (group) {
            var buttons = $$('[data-tab]', group);

            function select(btn, remember) {
                buttons.forEach(function (b) {
                    var on = b === btn;
                    var id = b.getAttribute('data-tab');
                    b.setAttribute('aria-selected', on ? 'true' : 'false');
                    var panel = document.getElementById(id);
                    if (panel) panel.hidden = !on;
                    /* A section's own subsection list travels with it, so the
                       rail only ever offers links that go somewhere visible. */
                    $$('[data-sub-for="' + id + '"]').forEach(function (sub) {
                        sub.hidden = !on;
                    });
                });
                /* Settings reloads itself after connecting an album, and the
                   old behaviour dropped the reader back on the first tab every
                   time. Naming the section in the URL also makes it linkable. */
                if (remember && group.hasAttribute('data-tabs-hash')) {
                    history.replaceState(null, '', '#' + btn.getAttribute('data-tab'));
                }
            }

            buttons.forEach(function (btn) {
                btn.addEventListener('click', function () { select(btn, true); });
            });

            if (group.hasAttribute('data-tabs-hash')) {
                var wanted = decodeURIComponent(location.hash.slice(1));
                var match = buttons.filter(function (b) {
                    return b.getAttribute('data-tab') === wanted;
                })[0];
                if (match) select(match, false);
            }
        });
    }

    function initAccordions(root) {
        $$('[data-accordion-trigger]', root).forEach(function (btn) {
            btn.addEventListener('click', function () {
                var panel = document.getElementById(btn.getAttribute('data-accordion-trigger'));
                var open = btn.getAttribute('aria-expanded') === 'true';
                btn.setAttribute('aria-expanded', open ? 'false' : 'true');
                if (panel) panel.hidden = open;
            });
        });
    }

    function initModals(root) {
        $$('[data-modal-open]', root).forEach(function (btn) {
            btn.addEventListener('click', function () {
                V.openModal(btn.getAttribute('data-modal-open'));
            });
        });
        $$('[data-modal-close]', root).forEach(function (btn) {
            btn.addEventListener('click', function () {
                var el = btn.closest('.modal');
                if (el) V.closeModal(el);
            });
        });
        $$('.modal__backdrop', root).forEach(function (bd) {
            bd.addEventListener('click', function () {
                var el = bd.closest('.modal');
                if (el) V.closeModal(el);
            });
        });
    }

    V.enhance = function (root) {
        initTabs(root);
        initAccordions(root);
        initModals(root);
    };

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !openStack.length) return;
        var top = openStack[openStack.length - 1];
        if (top.classList.contains('modal')) V.closeModal(top);
    });

    document.addEventListener('DOMContentLoaded', function () {
        V.i18n = window.VIGNETTE_I18N || V.i18n;
        V.enhance(document);

        var toggle = $('#theme-toggle');
        if (toggle) toggle.addEventListener('click', V.toggleTheme);
    });

})(window, document);
