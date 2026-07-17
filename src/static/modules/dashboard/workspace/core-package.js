import { createDisksComponent } from './components/disks.js';
import { createGitHubComponent } from './components/github.js';
import { createNetworkComponent } from './components/network.js';
import { createPlayerComponent, PLAYER_SINGLE_INSTANCE } from './components/player.js';
import { createSystemInfoComponent } from './components/system-info.js';
import { createUptimeComponent } from './components/uptime.js';
import { createVibeComponent, VIBE_SINGLE_INSTANCE } from './components/vibe.js';

export const CORE_EXTENSION_ID = 'cuckoo.core.dashboard';

export const CORE_WIDGET_TYPES = Object.freeze([
    'builtin.dashboard.system-info',
    'builtin.dashboard.network',
    'builtin.dashboard.uptime',
    'builtin.dashboard.disks',
    'builtin.dashboard.vibe',
    'builtin.dashboard.player',
    'builtin.dashboard.github',
]);

export function registerCuckooExtension(api) {
    api.registerWidget('builtin.dashboard.system-info', { create: createSystemInfoComponent, singleInstance: true });
    api.registerWidget('builtin.dashboard.network', { create: createNetworkComponent, singleInstance: true });
    api.registerWidget('builtin.dashboard.uptime', { create: createUptimeComponent, singleInstance: true });
    api.registerWidget('builtin.dashboard.disks', { create: createDisksComponent, singleInstance: true });
    api.registerWidget('builtin.dashboard.vibe', { create: createVibeComponent, singleInstance: VIBE_SINGLE_INSTANCE });
    api.registerWidget('builtin.dashboard.player', { create: createPlayerComponent, singleInstance: PLAYER_SINGLE_INSTANCE });
    api.registerWidget('builtin.dashboard.github', { create: createGitHubComponent, singleInstance: true });
}
