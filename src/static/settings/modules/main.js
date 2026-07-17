import {bindClientEvents, connectSettingsWebSocket, setClientWorkspaces} from './clients.js';
import {loadSettings} from './controller.js';
import {bindSettingsEvents} from './events.js';
import {initExtensions} from './extensions.js';
import {initWorkspaces, loadWorkspaces} from './workspaces.js';
import {hasUnsavedChanges} from './state.js';

bindSettingsEvents();
bindClientEvents();
connectSettingsWebSocket();
loadSettings();
initWorkspaces({onWorkspacesChange: setClientWorkspaces});
initExtensions({onStateChange: () => loadWorkspaces()});

window.addEventListener('beforeunload', (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
});
