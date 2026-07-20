export const state = {
    connection: {
        online: true,
        lastAliveTs: Date.now(),
        restFailStreak: 0,
        // 必须大于 ping 周期（5s）：稀疏工作区上 pong 可能是唯一的存活信号，
        // 阈值比它小会让离线横幅每 5 秒闪烁一次。取两个周期加余量。
        staleMs: 12000,
        failStreak: 2,
    },
    health: {
        services: {},
        vibeStatusProvider: null,
    },
    dashboard: {
        lastRingArgs: null,
        lastModelsKey: '',
    },
    clock: {
        offPeakBadgeConfig: {
            enabled: true,
            ranges: [{ start: '00:00', end: '08:00' }],
        },
    },
    media: {
        lyrics: [],
        title: '',
        trackKey: '',
        lyricsKey: '',
        hydrateRequestedAt: 0,
        startTime: 0,
        position: 0,
        duration: 0,
        playing: false,
        lastLyricIdx: -2,
        lastPositionSource: 'none',
        lyricIndex: -1,
        lineIndex: -1,
        lineText: '',
        lineDuration: 0,
        lineElapsedAtSync: 0,
        lineSyncAt: 0,
        lineActive: false,
        marqueeActive: false,
        marqueeRaf: 0,
        lyricOffset: 1.5,
        lineHeight: 34,
        pendingIdx: -2,
        pendingSince: 0,
        debounceMs: 300,
    },
    websocket: {
        socket: null,
        retry: 1000,
        fallbackTimer: null,
        reconnectTimer: null,
        pingTs: 0,
        latency: -1,
    },
    navigation: {
        locked: false,
    },
    vibe: {
        active: false,
        syncedFromServer: false,
    },
    appearance: {
        themeData: null,
    },
    timers: {
        clock: null,
        offPeak: null,
        health: null,
        connectionWatchdog: null,
        ping: null,
    },
};
