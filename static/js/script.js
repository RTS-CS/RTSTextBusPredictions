// Live clock
function updateClock() {
    const now = new Date();
    const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const clock = document.getElementById("clock");
    if (clock) {
        clock.textContent = timeStr;
    }
}
setInterval(updateClock, 1000);
window.onload = updateClock;

// Auto-refresh predictions every 60 seconds
setInterval(() => {
    fetch('/refresh', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    }).then(() => {
        window.location.reload();
    });
}, 60000);
function clearChat() {
    fetch('/clear', { method: 'POST' })
        .then(() => {
            window.location.reload();
        })
        .catch(err => console.error('Failed to clear chat:', err));
}
