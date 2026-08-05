/* ===========================================================================
   Vignette — shared application actions
   ---------------------------------------------------------------------------
   Thin wrappers over the device API plus the handful of display actions that
   more than one page needs. Page-specific behaviour lives in its template.
   =========================================================================== */

(function (window, document) {
    'use strict';

    var V = window.V = window.V || {};

    /* ── API helper ─────────────────────────────────────────────────────── */

    /**
     * Call the device API and resolve with the parsed JSON body.
     *
     * Rejects with an Error carrying the server's `error` field when there is
     * one, so callers can surface a real message instead of "[object Object]".
     * A 401 means the session expired — bounce to the login page rather than
     * showing a toast the user cannot act on.
     */
    V.api = function (path, options) {
        options = options || {};
        var init = { method: options.method || 'GET', cache: 'no-store' };

        if (options.body !== undefined && !(options.body instanceof FormData)) {
            init.headers = { 'Content-Type': 'application/json' };
            init.body = JSON.stringify(options.body);
        } else if (options.body instanceof FormData) {
            init.body = options.body;
        }

        return fetch(path, init).then(function (res) {
            return res.text().then(function (text) {
                var data = {};
                if (text) {
                    try { data = JSON.parse(text); }
                    catch (e) { data = { error: text.slice(0, 300) }; }
                }

                if (res.status === 401) {
                    window.location.href = data.redirect || '/auth/login';
                    throw new Error(data.error || 'Unauthorized');
                }
                if (!res.ok || data.error) {
                    throw new Error(data.error || ('HTTP ' + res.status));
                }
                return data;
            });
        });
    };

    /** Toast the message of a rejected V.api() call. */
    V.fail = function (err) {
        V.toast((err && err.message) || String(err), 'danger');
    };

    /* ── Display actions ────────────────────────────────────────────────── */

    /** Push a stored photo to the e-paper panel. */
    V.displayImage = function (filename, btn) {
        var restore = btn ? V.busy(btn, V.t('updating', 'Updating')) : null;
        var pending = V.toast(V.t('sending_to_epaper'), 'info', { duration: 0 });

        return V.api('/api/display', { method: 'POST', body: { filename: filename } })
            .then(function () {
                V.toast(V.t('displayed_ok'), 'success');
                document.dispatchEvent(new CustomEvent('vignette:displayed', {
                    detail: { filename: filename }
                }));
            })
            .catch(V.fail)
            .then(function () {
                pending.dismiss();
                if (restore) restore();
            });
    };

    V.clearDisplay = function () {
        return V.confirm(V.t('confirm_clear'), {
            title: V.t('clear'),
            confirmText: V.t('clear'),
            danger: true
        }).then(function (ok) {
            if (!ok) return;
            var pending = V.toast(V.t('clearing'), 'info', { duration: 0 });
            return V.api('/api/clear', { method: 'POST' })
                .then(function () { V.toast(V.t('cleared_ok', 'Display cleared'), 'success'); })
                .catch(V.fail)
                .then(function () { pending.dismiss(); });
        });
    };

    V.sleepDisplay = function () {
        return V.api('/api/sleep', { method: 'POST' })
            .then(function () { V.toast(V.t('sleep_ok'), 'success'); })
            .catch(V.fail);
    };

    V.displayTest = function () {
        return V.confirm(V.t('confirm_test'), {
            title: V.t('test_pattern'),
            confirmText: V.t('send', 'Send')
        }).then(function (ok) {
            if (!ok) return;
            var pending = V.toast(V.t('sending_test'), 'info', { duration: 0 });
            return V.api('/api/display/test', { method: 'POST' })
                .then(function () { V.toast(V.t('displayed_ok'), 'success'); })
                .catch(V.fail)
                .then(function () { pending.dismiss(); });
        });
    };

    /* ── Formatting ─────────────────────────────────────────────────────── */

    V.formatBytes = function (bytes) {
        if (!bytes && bytes !== 0) return '—';
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1024 / 1024).toFixed(1) + ' MB';
    };

    /** Cache-busting query for an image whose bytes change behind a fixed URL. */
    V.bust = function (url) {
        return url + (url.indexOf('?') === -1 ? '?' : '&') + 't=' + Date.now();
    };

    /* ── Language ───────────────────────────────────────────────────────── */

    V.setLanguage = function (lang) {
        return V.api('/api/lang', { method: 'POST', body: { lang: lang } })
            .then(function () { window.location.reload(); })
            .catch(V.fail);
    };

})(window, document);
