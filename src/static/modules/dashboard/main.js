import { initAppearance } from './appearance.js';
import { startClock } from './clock.js';
import { startConnectionMonitoring } from './connection.js';
import { bindDashboardActions } from './events.js';
import { initLyrics } from './lyrics.js';
import { initNavigation } from './navigation.js';
import { initVibe } from './vibe.js';
import { startWebSocket } from './ws.js';

function bootstrapDashboard() {
    bindDashboardActions();
    startConnectionMonitoring();
    startClock();
    initLyrics();
    initVibe();
    initAppearance();
    initNavigation();
    startWebSocket();
}

bootstrapDashboard();
