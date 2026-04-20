/**
 * InstaManager v2.0 — Client-Side JavaScript
 *
 * Handles SocketIO connection, sidebar toggle,
 * and toast notifications. Meta Graph API edition.
 */

// ═══ SocketIO Connection ═══════════════════════════════════════════════════
const socket = io({ transports: ['websocket', 'polling'] });
window.socket = socket;

socket.on('connect', () => {
    console.log('[InstaManager] SocketIO connected');
});

socket.on('disconnect', () => {
    console.log('[InstaManager] SocketIO disconnected');
});

socket.on('server_message', (data) => {
    console.log('[Server]', data.message);
});

// ═══ Sidebar Toggle (Mobile) ══════════════════════════════════════════════
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    const isOpen = !sidebar.classList.contains('-translate-x-full');

    if (isOpen) {
        sidebar.classList.add('-translate-x-full');
        overlay.classList.add('hidden');
    } else {
        sidebar.classList.remove('-translate-x-full');
        overlay.classList.remove('hidden');
    }
}

// ═══ Auto-dismiss Flash Messages ═════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.flash-msg').forEach(msg => {
        setTimeout(() => {
            msg.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
            msg.style.opacity = '0';
            msg.style.transform = 'translateY(-8px)';
            setTimeout(() => msg.remove(), 300);
        }, 5000);
    });
});

// ═══ Toast Notifications ═════════════════════════════════════════════════
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container') || createToastContainer();
    const toast = document.createElement('div');

    const colors = {
        success: 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300',
        error: 'bg-red-500/10 border-red-500/20 text-red-300',
        warning: 'bg-amber-500/10 border-amber-500/20 text-amber-300',
        info: 'bg-brand-500/10 border-brand-500/20 text-brand-300'
    };

    toast.className = `px-4 py-3 rounded-xl text-sm border ${colors[type] || colors.info} animate-slide-in`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(16px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}

function createToastContainer() {
    const c = document.createElement('div');
    c.id = 'toast-container';
    c.className = 'fixed top-4 right-4 z-50 space-y-2 max-w-sm';
    document.body.appendChild(c);
    return c;
}
