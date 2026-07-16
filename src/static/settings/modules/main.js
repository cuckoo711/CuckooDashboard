import {bindClientEvents, connectSettingsWebSocket, setClientWorkspaces} from './clients.js';
import {loadSettings} from './controller.js';
import {bindSettingsEvents} from './events.js';
import {initWorkspaces} from './workspaces.js';
import {hasUnsavedChanges} from './state.js';

bindSettingsEvents();
bindClientEvents();
connectSettingsWebSocket();
loadSettings();
initWorkspaces({onWorkspacesChange: setClientWorkspaces});

window.addEventListener('beforeunload', (event) => {
    if (!hasUnsavedChanges()) return;
    event.preventDefault();
    event.returnValue = '';
});
