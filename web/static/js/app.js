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
    var bgClass = {
        'success': 'bg-success',
        'danger': 'bg-danger',
        'warning': 'bg-warning text-dark',
        'info': 'bg-info text-dark',
    }[type] || 'bg-info text-dark';

    var html =
        '<div id="' + toastId + '" class="toast align-items-center ' + bgClass + ' text-white border-0" role="alert">' +
            '<div class="d-flex">' +
                '<div class="toast-body">' + message + '</div>' +
                '<button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>' +
            '</div>' +
        '</div>';
    container.insertAdjacentHTML('beforeend', html);

    var toastEl = document.getElementById(toastId);
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
