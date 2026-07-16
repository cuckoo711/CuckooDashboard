import {bindClientEvents, connectSettingsWebSocket} from './clients.js';
import {loadSettings} from './controller.js';
import {bindSettingsEvents} from './events.js';

bindSettingsEvents();
bindClientEvents();
connectSettingsWebSocket();
loadSettings();
