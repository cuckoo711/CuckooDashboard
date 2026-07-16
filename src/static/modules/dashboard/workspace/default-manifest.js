export const BUILTIN_WIDGET_TYPES = Object.freeze([
    'builtin.system.info',
    'builtin.system.network',
    'builtin.system.uptime',
    'builtin.system.disks',
    'builtin.media.player',
    'builtin.github.contributions',
]);

export const DEFAULT_WORKSPACE_MANIFEST = Object.freeze({
    id: 'main',
    version: 1,
    required: true,
    sources: ['system.snapshot', 'media.playback', 'github.contributions', 'dashboard.aggregate'],
    widgets: [
        { id: 'system-info', type: 'builtin.system.info', slot: 'main', sources: ['system.snapshot'], channels: [] },
        { id: 'system-network', type: 'builtin.system.network', slot: 'main', sources: ['system.snapshot'], channels: [] },
        { id: 'system-uptime', type: 'builtin.system.uptime', slot: 'main', sources: ['system.snapshot'], channels: [] },
        { id: 'system-disks', type: 'builtin.system.disks', slot: 'main', sources: ['system.snapshot'], channels: [] },
        { id: 'media-player', type: 'builtin.media.player', slot: 'main', sources: ['media.playback'], channels: ['media.lyric'] },
        { id: 'github-contributions', type: 'builtin.github.contributions', slot: 'main', sources: ['github.contributions'], channels: [] },
    ],
});
