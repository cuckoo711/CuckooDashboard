import { applyLyricFrame, drawLyric } from '../../lyrics.js';

export const PLAYER_SINGLE_INSTANCE = true;

export function createPlayerComponent() {
    let root = null;

    return {
        mount(context) {
            if (root) return root;
            root = document.createElement('div');
            root.className = 'card lyric-wrap';
            root.id = 'lyricCard';
            root.dataset.workspaceSlot = context.slot;
            root.dataset.singleInstanceBridge = 'player';
            root.innerHTML = '<div class="card-head"><span class="card-head-l"><span class="bar"></span>播放器<span class="svc-dot" id="dot-media"></span></span>'
                + '<span class="card-head-r"><a class="player-btn music-stage-link" href="/music" title="打开音乐舞台">🎛</a>'
                + '<span class="player-ctrl"><button class="player-btn" data-action="player-control" data-player-action="prev" title="上一首">⏮</button>'
                + '<button class="player-btn" data-action="player-control" data-player-action="toggle" title="播放/暂停">⏯</button>'
                + '<button class="player-btn" data-action="player-control" data-player-action="next" title="下一首">⏭</button></span></span></div>'
                + '<div class="card-body lyric-text" id="lyricText"><div class="lyric-scroll" id="lyricScroll"></div><div class="lyric-idle" id="lyricIdle">未在播放</div></div>'
                + '<div class="card-foot"><span class="card-foot-l" id="lyricTitle">--</span><span class="lyric-offset-wrap">'
                + '<button class="lyric-offset-btn" data-action="adjust-lyric-offset" data-delta="-0.5">−</button>'
                + '<span class="lyric-offset-val" id="lyricOffsetVal">0.0</span>'
                + '<button class="lyric-offset-btn" data-action="adjust-lyric-offset" data-delta="0.5">+</button></span>'
                + '<span class="card-foot-r" id="lyricArtist">--</span></div>';
            context.root.appendChild(root);
            return root;
        },
        onData(payload, source) {
            if (!root) return;
            if (source === 'media.lyric') applyLyricFrame(payload || {});
            else drawLyric(payload);
        },
        destroy() {
            root?.remove();
            root = null;
        },
    };
}
