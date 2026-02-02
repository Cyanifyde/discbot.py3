// Old Internet Aesthetic JavaScript
// Simple utilities for web UI

// Confirmation dialogs for dangerous actions
function confirmAction(message) {
    return confirm(message);
}

// Auto-refresh for certain pages
function setupAutoRefresh(interval) {
    if (interval > 0) {
        setTimeout(() => {
            location.reload();
        }, interval * 1000);
    }
}

// Simple form validation
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return true;

    const required = form.querySelectorAll('[required]');
    for (let field of required) {
        if (!field.value.trim()) {
            alert(`Please fill in: ${field.name}`);
            field.focus();
            return false;
        }
    }
    return true;
}

// Toast notifications (old-school alert style)
function showNotification(message, type) {
    const prefix = type === 'error' ? '❌ ' : type === 'success' ? '✅ ' : 'ℹ️ ';
    alert(prefix + message);
}

// Dynamic table filtering
function filterTable(inputId, tableId) {
    const input = document.getElementById(inputId);
    const table = document.getElementById(tableId);

    if (!input || !table) return;

    input.addEventListener('keyup', function() {
        const filter = this.value.toLowerCase();
        const rows = table.getElementsByTagName('tr');

        for (let i = 1; i < rows.length; i++) {
            const row = rows[i];
            const text = row.textContent.toLowerCase();
            row.style.display = text.includes(filter) ? '' : 'none';
        }
    });
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', function() {
    // Add hover effects
    const buttons = document.querySelectorAll('.button');
    buttons.forEach(button => {
        button.addEventListener('mouseenter', function() {
            this.style.cursor = 'pointer';
        });
    });

    // Confirm dangerous actions
    const dangerButtons = document.querySelectorAll('.button.danger');
    dangerButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            if (!confirm('Are you sure? This action cannot be undone.')) {
                e.preventDefault();
                return false;
            }
        });
    });
});
