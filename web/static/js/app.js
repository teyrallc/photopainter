/* Vignette - Client-side JavaScript */

/**
 * Show a toast notification.
 */
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toastId = 'toast-' + Date.now();
    const bgClass = {
        'success': 'bg-success',
        'danger': 'bg-danger',
        'warning': 'bg-warning text-dark',
        'info': 'bg-info text-dark',
    }[type] || 'bg-info text-dark';

    const html = `
        <div id="${toastId}" class="toast align-items-center ${bgClass} text-white border-0" role="alert">
            <div class="d-flex">
                <div class="toast-body">${message}</div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
            </div>
        </div>
    `;
    container.insertAdjacentHTML('beforeend', html);

    const toastEl = document.getElementById(toastId);
    const toast = new bootstrap.Toast(toastEl, { delay: 4000 });
    toast.show();

    toastEl.addEventListener('hidden.bs.toast', () => toastEl.remove());
}

/**
 * Display an image on the e-paper display.
 */
function displayImage(filename) {
    showToast('正在發送到電子紙...', 'info');

    fetch('/api/display', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: filename })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) {
            showToast('圖片已顯示到電子紙！', 'success');
        } else {
            showToast('顯示失敗：' + data.error, 'danger');
        }
    })
    .catch(err => {
        showToast('連線錯誤：' + err, 'danger');
    });
}

/**
 * Clear the e-paper display.
 */
function clearDisplay() {
    if (!confirm('確定要清除螢幕嗎？')) return;

    showToast('正在清除螢幕...', 'info');
    fetch('/api/clear', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('螢幕已清除', 'success');
            } else {
                showToast('清除失敗：' + data.error, 'danger');
            }
        })
        .catch(err => showToast('連線錯誤：' + err, 'danger'));
}

/**
 * Put display to sleep.
 */
function sleepDisplay() {
    fetch('/api/sleep', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('螢幕已進入休眠', 'success');
            } else {
                showToast('休眠失敗：' + data.error, 'danger');
            }
        })
        .catch(err => showToast('連線錯誤：' + err, 'danger'));
}

/**
 * Photo navigation: next photo.
 */
function photoNext() {
    showToast('載入下一張...', 'info');
    fetch('/api/photo/next', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                updatePhotoUI(data.photo);
                showToast('已顯示下一張', 'success');
            } else {
                showToast(data.error || '操作失敗', 'danger');
            }
        })
        .catch(err => showToast('連線錯誤：' + err, 'danger'));
}

/**
 * Photo navigation: previous photo.
 */
function photoPrev() {
    showToast('載入上一張...', 'info');
    fetch('/api/photo/prev', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                updatePhotoUI(data.photo);
                showToast('已顯示上一張', 'success');
            } else {
                showToast(data.error || '操作失敗', 'danger');
            }
        })
        .catch(err => showToast('連線錯誤：' + err, 'danger'));
}

/**
 * Photo navigation: show latest photo.
 */
function photoLatest() {
    showToast('載入最新照片...', 'info');
    fetch('/api/photo/latest', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                updatePhotoUI(data.photo);
                showToast('已顯示最新照片', 'success');
            } else {
                showToast(data.error || '操作失敗', 'danger');
            }
        })
        .catch(err => showToast('連線錯誤：' + err, 'danger'));
}

/**
 * Send test pattern to e-paper.
 */
function displayTest() {
    if (!confirm('確定要發送測試圖案到電子紙嗎？')) return;

    showToast('正在發送測試圖案...', 'info');
    fetch('/api/display/test', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showToast('測試圖案已顯示！', 'success');
            } else {
                showToast('測試失敗：' + (data.error || '未知錯誤'), 'danger');
            }
        })
        .catch(err => showToast('連線錯誤：' + err, 'danger'));
}

/**
 * Update photo navigation UI after a navigation action.
 */
function updatePhotoUI(photo) {
    if (!photo) return;

    const indexEl = document.getElementById('photo-index');
    const totalEl = document.getElementById('photo-total');
    const previewDiv = document.getElementById('photo-preview');

    if (indexEl) indexEl.textContent = photo.current_index + 1;
    if (totalEl) totalEl.textContent = photo.total;

    if (previewDiv && photo.current_image) {
        previewDiv.innerHTML = `
            <div class="epaper-frame">
                <img src="/output/${photo.current_image}" class="img-fluid rounded"
                     alt="Current" style="max-height: 200px; object-fit: contain;">
            </div>
            <p class="mt-2 mb-0"><strong>${photo.current_image}</strong></p>
        `;
    }
}
