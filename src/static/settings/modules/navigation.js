const STORAGE_KEY = 'cuckoo.settings.active_section';

const DEFAULT_SECTION = 'general';

function sectionButtons() {
    return Array.from(document.querySelectorAll('[data-settings-nav]'));
}

function sectionViews() {
    return Array.from(document.querySelectorAll('[data-settings-view]'));
}

export function getActiveSettingsSection() {
    return document.querySelector('[data-settings-nav].is-active')?.dataset.settingsNav || DEFAULT_SECTION;
}

export function setActiveSettingsSection(sectionId = DEFAULT_SECTION) {
    const requested = String(sectionId || DEFAULT_SECTION);
    const buttons = sectionButtons();
    const views = sectionViews();
    if (!buttons.length || !views.length) return DEFAULT_SECTION;
    const available = new Set(buttons.map((btn) => btn.dataset.settingsNav));
    const active = available.has(requested) ? requested : DEFAULT_SECTION;
    buttons.forEach((btn) => {
        btn.classList.toggle('is-active', btn.dataset.settingsNav === active);
    });
    views.forEach((view) => {
        const match = view.dataset.settingsView === active;
        view.classList.toggle('is-active', match);
        view.hidden = !match;
    });
    try { localStorage.setItem(STORAGE_KEY, active); } catch (_error) {}
    return active;
}

export function bindSettingsNavigation() {
    let initial = DEFAULT_SECTION;
    try { initial = localStorage.getItem(STORAGE_KEY) || DEFAULT_SECTION; } catch (_error) {}
    setActiveSettingsSection(initial);
    document.addEventListener('click', (event) => {
        const button = event.target.closest?.('[data-settings-nav]');
        if (!button) return;
        event.preventDefault();
        setActiveSettingsSection(button.dataset.settingsNav);
    });
}
