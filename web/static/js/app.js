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
     * Describe a reply that did not come from this device.
     *
     * Everything the frame itself answers is JSON. Anything else was produced
     * by something in between — a tunnel that could not reach the Pi, a proxy,
     * a captive portal — and its body is a full HTML error page. Pasting 300
     * characters of that into the interface is not an error message: it is
     * what put a raw "<!DOCTYPE html> <!--[if lt IE 7]>…" into the settings
     * page's save line when the Cloudflare tunnel returned a 502.
     *
     * Such a page does carry one useful sentence — its <title>, which reads
     * like "example.com | 502: Bad gateway" — so that is used when it is
     * short enough to be a message, and the status code alone when it is not.
     */
    function describeGateway(status, text) {
        var title = /<title[^>]*>([^<]*)<\/title>/i.exec(text || '');
        var headline = title && title[1] ? title[1].replace(/\s+/g, ' ').trim() : '';
        if (headline && headline.length <= 90) {
            return headline;
        }
        return V.t('gateway_error', 'The frame could not be reached') +
               (status ? ' (HTTP ' + status + ')' : '');
    }

    /* Exposed so the one place that cannot use V.api — the Drive listing, which
       needs its own 401 handling — describes a non-JSON reply the same way
       rather than surfacing a raw JSON parse error. */
    V.gatewayMessage = describeGateway;

    /**
     * Call the device API and resolve with the parsed JSON body.
     *
     * Rejects with an Error carrying the server's `error` field when there is
     * one, so callers can surface a real message instead of "[object Object]".
     *
     * A 401 only bounces to the login page when the server names where to go.
     * The session wall does; a route rejecting a credential the user just
     * typed does not — and treating those alike signed people out of the page
     * for mistyping their current password, losing the form with it.
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
                    catch (e) { data = { error: describeGateway(res.status, text) }; }
                }

                if (res.status === 401 && data.redirect) {
                    window.location.href = data.redirect;
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
