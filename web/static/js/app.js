/* Vignette - Client-side JavaScript */

/**
 * Get i18n string from the global translations object.
 * Falls back to the key itself if not found.
 */
function _t(key) {
    return (window.VIGNETTE_I18N && window.VIGNETTE_I18N[key]) || key;
}

/**
 * Show a toast notification.
 */
function showToast(message, type) {
    type = type || 'info';
    var container = document.getElementById('toast-container');
    if (!container) return;

    var toastId = 'toast-' + Date.now();
    // Text colour must follow the background: light backgrounds (warning/info)
    // need dark text and a dark close button; dark backgrounds (success/danger)
    // need white. The old code hardcoded white text on every toast, so warning
    // and info toasts were white-on-light and unreadable.
    var styles = {
        'success': { bg: 'bg-success', text: 'text-white', close: 'btn-close-white' },
        'danger':  { bg: 'bg-danger',  text: 'text-white', close: 'btn-close-white' },
        'warning': { bg: 'bg-warning', text: 'text-dark',  close: '' },
        'info':    { bg: 'bg-info',    text: 'text-dark',  close: '' },
    };
    var s = styles[type] || styles.info;

    // Build via DOM + textContent so a message containing server error text /
    // filenames can never inject HTML (defense-in-depth).
    var toastEl = document.createElement('div');
    toastEl.id = toastId;
    toastEl.className = 'toast align-items-center ' + s.bg + ' ' + s.text + ' border-0';
    toastEl.setAttribute('role', 'alert');
    toastEl.setAttribute('aria-live', 'assertive');
    toastEl.setAttribute('aria-atomic', 'true');
    var flex = document.createElement('div');
    flex.className = 'd-flex';
    var body = document.createElement('div');
    body.className = 'toast-body';
    body.textContent = message;
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-close ' + s.close + ' me-2 m-auto';
    btn.setAttribute('data-bs-dismiss', 'toast');
    btn.setAttribute('aria-label', 'Close');
    flex.appendChild(body); flex.appendChild(btn); toastEl.appendChild(flex);
    container.appendChild(toastEl);

    var toast = new bootstrap.Toast(toastEl, { delay: 4000 });
    toast.show();

    toastEl.addEventListener('hidden.bs.toast', function() { toastEl.remove(); });
}

/**
 * Display an image on the e-paper display.
 */
function displayImage(filename) {
    showToast(_t('sending_to_epaper'), 'info');

    fetch('/api/display', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: filename })
    })
    .then(function(r) { return r.json(); })
    .then(function(data) {
        if (data.success) {
            showToast(_t('displayed_ok'), 'success');
        } else {
            showToast(data.error, 'danger');
        }
    })
    .catch(function(err) {
        showToast('Error: ' + err, 'danger');
    });
}

/**
 * Clear the e-paper display.
 */
function clearDisplay() {
    if (!confirm(_t('confirm_clear'))) return;

    showToast(_t('clearing'), 'info');
    fetch('/api/clear', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) showToast('OK', 'success');
            else showToast(data.error, 'danger');
        })
        .catch(function(err) { showToast('Error: ' + err, 'danger'); });
}

/**
 * Put display to sleep.
 */
function sleepDisplay() {
    fetch('/api/sleep', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) showToast(_t('sleep_ok'), 'success');
            else showToast(data.error, 'danger');
        })
        .catch(function(err) { showToast('Error: ' + err, 'danger'); });
}

/**
 * Photo navigation: next photo.
 */
function photoNext() {
    showToast(_t('loading_next'), 'info');
    fetch('/api/photo/next', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                updatePhotoUI(data.photo);
                showToast('OK', 'success');
            } else {
                showToast(data.error || 'Failed', 'danger');
            }
        })
        .catch(function(err) { showToast('Error: ' + err, 'danger'); });
}

/**
 * Photo navigation: previous photo.
 */
function photoPrev() {
    showToast(_t('loading_prev'), 'info');
    fetch('/api/photo/prev', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                updatePhotoUI(data.photo);
                showToast('OK', 'success');
            } else {
                showToast(data.error || 'Failed', 'danger');
            }
        })
        .catch(function(err) { showToast('Error: ' + err, 'danger'); });
}

/**
 * Photo navigation: show latest photo.
 */
function photoLatest() {
    showToast(_t('loading_latest'), 'info');
    fetch('/api/photo/latest', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) {
                updatePhotoUI(data.photo);
                showToast('OK', 'success');
            } else {
                showToast(data.error || 'Failed', 'danger');
            }
        })
        .catch(function(err) { showToast('Error: ' + err, 'danger'); });
}

/**
 * Send test pattern to e-paper.
 */
function displayTest() {
    if (!confirm(_t('confirm_test'))) return;

    showToast(_t('sending_test'), 'info');
    fetch('/api/display/test', { method: 'POST' })
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.success) showToast('OK', 'success');
            else showToast(data.error, 'danger');
        })
        .catch(function(err) { showToast('Error: ' + err, 'danger'); });
}

/**
 * Update photo navigation UI after a navigation action.
 */
function updatePhotoUI(photo) {
    if (!photo) return;

    var indexEl = document.getElementById('photo-index');
    var totalEl = document.getElementById('photo-total');
    var previewDiv = document.getElementById('photo-preview');

    if (indexEl) indexEl.textContent = photo.current_index + 1;
    if (totalEl) totalEl.textContent = photo.total;

    if (previewDiv && photo.current_image) {
        previewDiv.innerHTML =
            '<div class="epaper-frame">' +
                '<img src="/output/' + photo.current_image + '" class="img-fluid rounded" ' +
                     'alt="Current" style="max-height: 200px; object-fit: contain;">' +
            '</div>' +
            '<p class="mt-2 mb-0"><strong>' + photo.current_image + '</strong></p>';
    }
}
