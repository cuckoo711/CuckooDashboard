export async function captureAndSendScreenshot(requestId, getSocket) {
    try {
        const renderer = globalThis.html2canvas;
        if (typeof renderer !== 'function') {
            console.error('[screenshot] html2canvas not loaded');
            return;
        }
        const canvas = await renderer(document.body, {
            backgroundColor: null,
            scale: window.devicePixelRatio || 1,
            useCORS: true,
        });
        const socket = getSocket();
        if (socket && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({
                type: 'screenshot_data',
                request_id: requestId,
                data: canvas.toDataURL('image/png'),
            }));
        }
    } catch (error) {
        console.error('[screenshot] failed:', error);
    }
}
