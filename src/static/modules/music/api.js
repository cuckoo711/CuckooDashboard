import { createSecureFetch } from '../shared/http.js';
import { createPlayerController } from '../shared/player.js';

export const secureFetch = createSecureFetch();
export const playerControl = createPlayerController(secureFetch);

export function reloadMedia(applyMedia) {
    return secureFetch('/api/media/reload', { method: 'POST' })
        .then((response) => response.json())
        .then(applyMedia)
        .catch((error) => console.error('[media reload]', error));
}
