import { createDisksComponent } from './components/disks.js';
import { createGitHubComponent } from './components/github.js';
import { createNetworkComponent } from './components/network.js';
import { createPlayerComponent, PLAYER_SINGLE_INSTANCE } from './components/player.js';
import { createSystemInfoComponent } from './components/system-info.js';
import { createUptimeComponent } from './components/uptime.js';

const registrations = new Map([
    ['builtin.system.info', { create: createSystemInfoComponent }],
    ['builtin.system.network', { create: createNetworkComponent }],
    ['builtin.system.uptime', { create: createUptimeComponent }],
    ['builtin.system.disks', { create: createDisksComponent }],
    ['builtin.media.player', { create: createPlayerComponent, singleInstance: PLAYER_SINGLE_INSTANCE }],
    ['builtin.github.contributions', { create: createGitHubComponent }],
]);

export const BUILTIN_COMPONENT_TYPES = Object.freeze([...registrations.keys()]);

export const componentRegistry = Object.freeze({
    has(type) {
        return registrations.has(type);
    },
    get(type) {
        return registrations.get(type) || null;
    },
    types() {
        return [...registrations.keys()];
    },
});
