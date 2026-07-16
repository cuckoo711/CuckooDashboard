export async function requestJson(url, options = {}) {
    const requestOptions = {...options};
    requestOptions.credentials = 'same-origin';
    requestOptions.headers = {...(options.headers || {})};
    requestOptions.headers['X-Requested-With'] = 'CuckooSettings';
    if (requestOptions.body && typeof requestOptions.body !== 'string') {
        requestOptions.headers['Content-Type'] = 'application/json';
        requestOptions.body = JSON.stringify(requestOptions.body);
    }

    const response = await fetch(url, requestOptions);
    let data = {};
    try {
        data = await response.json();
    } catch (_error) {
        // Structured API errors are preferred, but an empty/non-JSON response
        // still gets a useful HTTP status error below.
    }
    if (!response.ok) {
        const error = new Error((data.error && data.error.message) || `HTTP ${response.status}`);
        error.payload = data;
        error.status = response.status;
        throw error;
    }
    return data;
}
