export function createPlayerController(secureFetch) {
    return function playerControl(action) {
        return secureFetch('/api/player/' + encodeURIComponent(action), { method: 'POST' })
            .catch((error) => console.error('[player]', error));
    };
}
