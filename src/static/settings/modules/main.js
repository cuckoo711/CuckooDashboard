import {bindClientEvents, connectSettingsWebSocket, setClientWorkspaces} from './clients.js';
import {loadSettings} from './controller.js';
import {bindDeviceEvents, setDeviceWorkspaces} from './devices.js';
import {bindSettingsEvents} from './events.js';
import {initExtensions} from './extensions.js';
import {bindSettingsNavigation} from './navigation.js';
import {initWorkspaces, loadWorkspaces} from './workspaces.js';
import {hasUnsavedChanges} from './state.js';

function bootSettings() {
    bindSettingsNavigation();
    bindSettingsEvents();
    bindClientEvents();
    bindDeviceEvents();
    connectSettingsWebSocket();
    loadSettings();
    initWorkspaces({
        onWorkspacesChange: (workspaces) => {
            setClientWorkspaces(workspaces);
            setDeviceWorkspaces(workspaces);
        },
    });
    initExtensions({onStateChange: () => loadWorkspaces()});
}

try {
    bootSettings();
} catch (error) {
    console.error('[settings] boot failed:', error);
    const loading = document.getElementById('loadingState');
    const err = document.getElementById('errorState');
    if (loading) loading.hidden = true;
    if (err) {
        err.hidden = false;
        err.textContent = `设置页启动失败：${error?.message || error}`;
    }
}

window.addEventListener('beforeunload', (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
});
