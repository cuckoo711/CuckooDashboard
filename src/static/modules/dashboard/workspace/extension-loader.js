import {
    CORE_EXTENSION_ID,
    CORE_WIDGET_TYPES,
    registerCuckooExtension as registerCoreExtension,
} from './core-package.js';
import { componentRegistry } from './registry.js';

const CATALOG_URL = '/api/runtime/extensions';

function reasonOf(error) {
    return error instanceof Error ? error.message : String(error || 'unknown extension error');
}

function validateWidgetTypes(value, extensionId) {
    if (!Array.isArray(value)) throw new TypeError(`extension ${extensionId} widget_types must be an array`);
    const widgetTypes = value.map((type) => {
        if (typeof type !== 'string' || !type.trim()) {
            throw new TypeError(`extension ${extensionId} contains an invalid widget type`);
        }
        return type.trim();
    });
    if (new Set(widgetTypes).size !== widgetTypes.length) {
        throw new Error(`extension ${extensionId} contains duplicate widget types`);
    }
    return widgetTypes;
}

function pageUrl(catalogResponseUrl = '') {
    const location = globalThis.location;
    if (location && typeof location.href === 'string' && location.href) return new URL(location.href);
    if (catalogResponseUrl) {
        try {
            return new URL(catalogResponseUrl);
        } catch (_error) {}
    }
    return new URL('http://localhost/');
}

function validateModuleUrl(moduleUrl, extensionId, baseUrl) {
    if (typeof moduleUrl !== 'string' || !moduleUrl.trim()) {
        throw new TypeError(`extension ${extensionId} module_url must be a non-empty string`);
    }
    const rawUrl = moduleUrl.trim();
    const resolved = new URL(rawUrl, baseUrl);
    if (resolved.origin !== baseUrl.origin) {
        throw new Error(`extension ${extensionId} module_url must be same-origin`);
    }
    if (resolved.username || resolved.password) {
        throw new Error(`extension ${extensionId} module_url must not contain credentials`);
    }
    const requiredPrefix = `/runtime/extensions/${encodeURIComponent(extensionId)}/assets/`;
    if (!resolved.pathname.startsWith(requiredPrefix)) {
        throw new Error(`extension ${extensionId} module_url must start with ${requiredPrefix}`);
    }
    return rawUrl;
}

function registerOwner(registry, ownerId, expectedTypes, register) {
    if (typeof registry.registerOwner === 'function') {
        return registry.registerOwner(ownerId, expectedTypes, register);
    }
    if (typeof registry.transaction === 'function') {
        return registry.transaction(ownerId, expectedTypes, register);
    }
    if (typeof registry.begin === 'function') {
        const pending = registry.begin(ownerId, expectedTypes);
        try {
            register(pending.api || pending);
            return pending.commit();
        } catch (error) {
            pending.rollback?.();
            throw error;
        }
    }
    throw new TypeError('registry does not support owner registration transactions');
}

async function defaultFetchCatalog(url, options) {
    if (typeof globalThis.fetch !== 'function') throw new Error('fetch is not available');
    return globalThis.fetch(url, options);
}

function defaultImportModule(moduleUrl) {
    return import(moduleUrl);
}

async function readCatalog(fetchCatalog) {
    const response = await fetchCatalog(CATALOG_URL, {
        method: 'GET',
        headers: { Accept: 'application/json' },
    });
    if (response && typeof response.json === 'function') {
        if (response.ok === false) {
            throw new Error(`extension catalog request failed with status ${response.status}`);
        }
        return { payload: await response.json(), responseUrl: response.url || '' };
    }
    return { payload: response, responseUrl: '' };
}

function markOwnerUnavailable(registry, widgetTypes, reason) {
    if (!widgetTypes.length || typeof registry.markUnavailable !== 'function') return;
    registry.markUnavailable(widgetTypes, reason);
}

export async function loadRuntimeExtensions({
    registry = componentRegistry,
    fetchCatalog = defaultFetchCatalog,
    importModule = defaultImportModule,
} = {}) {
    const summary = { loaded: [], failed: [], catalogError: null };

    try {
        registerOwner(registry, CORE_EXTENSION_ID, CORE_WIDGET_TYPES, registerCoreExtension);
        summary.loaded.push(CORE_EXTENSION_ID);
    } catch (error) {
        const reason = reasonOf(error);
        markOwnerUnavailable(registry, CORE_WIDGET_TYPES, reason);
        summary.failed.push({ id: CORE_EXTENSION_ID, widgetTypes: [...CORE_WIDGET_TYPES], reason });
        return summary;
    }

    let catalog;
    let responseUrl;
    try {
        ({ payload: catalog, responseUrl } = await readCatalog(fetchCatalog));
        if (!catalog || typeof catalog !== 'object' || Array.isArray(catalog)) {
            throw new TypeError('extension catalog must be an object');
        }
        if (catalog.api_version !== 1) throw new Error('extension catalog api_version must be 1');
        if (!Array.isArray(catalog.extensions)) throw new TypeError('extension catalog extensions must be an array');
    } catch (error) {
        summary.catalogError = reasonOf(error);
        return summary;
    }

    const baseUrl = pageUrl(responseUrl);
    for (const entry of catalog.extensions) {
        if (entry && entry.id === CORE_EXTENSION_ID) continue;

        let extensionId = '(unknown)';
        let widgetTypes = [];
        try {
            if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
                throw new TypeError('extension catalog entry must be an object');
            }
            if (typeof entry.id !== 'string' || !entry.id.trim()) {
                throw new TypeError('extension id must be a non-empty string');
            }
            extensionId = entry.id.trim();
            widgetTypes = validateWidgetTypes(entry.widget_types, extensionId);
            const moduleUrl = validateModuleUrl(entry.module_url, extensionId, baseUrl);
            const extensionModule = await importModule(moduleUrl);
            if (!extensionModule || typeof extensionModule.registerCuckooExtension !== 'function') {
                throw new TypeError(`extension ${extensionId} must export registerCuckooExtension(api)`);
            }
            registerOwner(registry, extensionId, widgetTypes, extensionModule.registerCuckooExtension);
            summary.loaded.push(extensionId);
        } catch (error) {
            const reason = reasonOf(error);
            markOwnerUnavailable(registry, widgetTypes, reason);
            summary.failed.push({ id: extensionId, widgetTypes: [...widgetTypes], reason });
        }
    }

    return summary;
}
