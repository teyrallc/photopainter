/* PhotoPainter - Client-side JavaScript */

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
