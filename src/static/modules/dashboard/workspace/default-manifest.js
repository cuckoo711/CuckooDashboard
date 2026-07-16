export const BUILTIN_WIDGET_TYPES = Object.freeze([
    'builtin.dashboard.system-info',
    'builtin.dashboard.network',
    'builtin.dashboard.uptime',
    'builtin.dashboard.disks',
    'builtin.dashboard.vibe',
    'builtin.dashboard.player',
    'builtin.dashboard.github',
]);

const sources = [
    { id: 'system.snapshot', kind: 'snapshot', legacy_message_type: 'system', default_interval_seconds: 1, active_interval_seconds: null },
    { id: 'dashboard.aggregate', kind: 'snapshot', legacy_message_type: 'dashboard_data', default_interval_seconds: 60, active_interval_seconds: 20 },
    { id: 'media.playback', kind: 'snapshot', legacy_message_type: 'media', default_interval_seconds: 1, active_interval_seconds: null },
    { id: 'github.contributions', kind: 'snapshot', legacy_message_type: 'github', default_interval_seconds: 1, active_interval_seconds: null },
];

const widget = (id, type, sourcesList, channels, layout, constraints) => ({
    id,
    type,
    slot: 'main',
    sources: sourcesList,
    channels,
    layout,
    constraints,
});

export const DEFAULT_WORKSPACE_MANIFEST = Object.freeze({
    id: 'main',
    version: 2,
    revision: 1,
    name: 'Main Dashboard',
    kind: 'builtin',
    required: true,
    grid: { columns: 16, rows: 15 },
    sources,
    widgets: [
        widget('system-info', 'builtin.dashboard.system-info', ['system.snapshot'], [],
            { x: 0, y: 0, width: 6, height: 5 },
            { min_width: 4, min_height: 4, max_width: 16, max_height: 15 }),
        widget('network', 'builtin.dashboard.network', ['system.snapshot'], [],
            { x: 6, y: 0, width: 2, height: 3 },
            { min_width: 2, min_height: 2, max_width: 16, max_height: 15 }),
        widget('uptime', 'builtin.dashboard.uptime', ['system.snapshot'], [],
            { x: 6, y: 3, width: 2, height: 2 },
            { min_width: 2, min_height: 2, max_width: 16, max_height: 15 }),
        widget('disks', 'builtin.dashboard.disks', ['system.snapshot'], [],
            { x: 0, y: 5, width: 8, height: 4 },
            { min_width: 4, min_height: 3, max_width: 16, max_height: 15 }),
        widget('token-card', 'builtin.dashboard.vibe', ['dashboard.aggregate'], [],
            { x: 8, y: 0, width: 8, height: 9 },
            { min_width: 6, min_height: 6, max_width: 16, max_height: 15 }),
        widget('player', 'builtin.dashboard.player', ['media.playback'], ['media.lyric'],
            { x: 0, y: 9, width: 8, height: 6 },
            { min_width: 6, min_height: 4, max_width: 16, max_height: 15 }),
        widget('github', 'builtin.dashboard.github', ['github.contributions'], [],
            { x: 8, y: 9, width: 8, height: 6 },
            { min_width: 6, min_height: 4, max_width: 16, max_height: 15 }),
    ],
});
