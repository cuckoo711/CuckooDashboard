import { createPlayerController } from '../shared/player.js';
import { cycleTheme } from './appearance.js';
import { secureFetch } from './connection.js';
import { adjustLyricOffset } from './lyrics.js';
import { toggleVibe } from './vibe.js';

const playerControl = createPlayerController(secureFetch);

export function bindDashboardActions() {
    document.addEventListener('click', (event) => {
        const target = event.target?.closest?.('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        if (action === 'toggle-vibe') {
            toggleVibe();
        } else if (action === 'player-control') {
            playerControl(target.dataset.playerAction || '');
        } else if (action === 'adjust-lyric-offset') {
            adjustLyricOffset(Number(target.dataset.delta || 0));
        } else if (action === 'cycle-theme') {
            cycleTheme();
        }
    });
}
