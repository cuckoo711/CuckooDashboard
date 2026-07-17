import { normalizeWidgetLifecycle } from './widget-sdk.js';

function requireIdentifier(value, label) {
    if (typeof value !== 'string' || !value.trim()) {
        throw new TypeError(`${label} must be a non-empty string`);
    }
    return value.trim();
}

function normalizeExpectedTypes(expectedTypes) {
    if (!expectedTypes || typeof expectedTypes === 'string' || !expectedTypes[Symbol.iterator]) {
        throw new TypeError('expectedTypes must be an iterable of widget types');
    }
    const types = Array.from(expectedTypes, (type) => requireIdentifier(type, 'widget type'));
    if (new Set(types).size !== types.length) throw new Error('expectedTypes contains duplicate widget types');
    return types;
}

function errorReason(reason) {
    if (reason instanceof Error) return reason.message;
    return String(reason || 'unavailable');
}

export function createComponentRegistry() {
    const registrations = new Map();
    const typeOwners = new Map();
    const ownerTypes = new Map();
    const unavailableReasons = new Map();

    function begin(ownerIdValue, expectedTypesValue) {
        const ownerId = requireIdentifier(ownerIdValue, 'ownerId');
        const expectedTypes = normalizeExpectedTypes(expectedTypesValue);
        const expected = new Set(expectedTypes);
        const staged = new Map();
        let active = true;

        if (ownerTypes.has(ownerId)) throw new Error(`owner already registered: ${ownerId}`);
        expectedTypes.forEach((type) => {
            if (registrations.has(type)) throw new Error(`widget type already registered: ${type}`);
        });

        function assertActive() {
            if (!active) throw new Error(`registration transaction is no longer active: ${ownerId}`);
        }

        const api = Object.freeze({
            ownerId,
            expectedTypes: Object.freeze([...expectedTypes]),
            registerWidget(typeValue, definition) {
                try {
                    assertActive();
                    const type = requireIdentifier(typeValue, 'widget type');
                    if (!expected.has(type)) throw new Error(`owner ${ownerId} cannot register undeclared widget type: ${type}`);
                    if (staged.has(type)) throw new Error(`widget type registered more than once by ${ownerId}: ${type}`);
                    if (registrations.has(type)) throw new Error(`widget type already registered: ${type}`);
                    if (!definition || typeof definition !== 'object') {
                        throw new TypeError(`widget registration must be an object: ${type}`);
                    }
                    if (typeof definition.create !== 'function') {
                        throw new TypeError(`widget registration requires create(): ${type}`);
                    }
                    const singleInstance = definition.singleInstance === undefined ? false : definition.singleInstance;
                    if (typeof singleInstance !== 'boolean') {
                        throw new TypeError(`singleInstance must be a boolean: ${type}`);
                    }
                    staged.set(type, Object.freeze({
                        create: () => normalizeWidgetLifecycle(definition.create(), type),
                        singleInstance,
                    }));
                } catch (error) {
                    rollback();
                    throw error;
                }
            },
        });

        function rollback() {
            if (!active) return false;
            active = false;
            staged.clear();
            return true;
        }

        function commit() {
            assertActive();
            const actualTypes = [...staged.keys()];
            const exactMatch = actualTypes.length === expectedTypes.length
                && expectedTypes.every((type) => staged.has(type));
            if (!exactMatch) {
                rollback();
                throw new Error(
                    `owner ${ownerId} registered [${actualTypes.join(', ')}], expected [${expectedTypes.join(', ')}]`,
                );
            }
            if (ownerTypes.has(ownerId)) {
                rollback();
                throw new Error(`owner already registered: ${ownerId}`);
            }
            for (const type of expectedTypes) {
                if (registrations.has(type)) {
                    rollback();
                    throw new Error(`widget type already registered: ${type}`);
                }
            }
            expectedTypes.forEach((type) => {
                registrations.set(type, staged.get(type));
                typeOwners.set(type, ownerId);
                unavailableReasons.delete(type);
            });
            ownerTypes.set(ownerId, Object.freeze([...expectedTypes]));
            active = false;
            staged.clear();
            return Object.freeze({ ownerId, types: Object.freeze([...expectedTypes]) });
        }

        return Object.freeze({
            ownerId,
            expectedTypes: Object.freeze([...expectedTypes]),
            api,
            registerWidget: api.registerWidget,
            commit,
            rollback,
        });
    }

    function transaction(ownerId, expectedTypes, register) {
        if (typeof register !== 'function') throw new TypeError('register must be a function');
        const pending = begin(ownerId, expectedTypes);
        try {
            const result = register(pending.api);
            if (result && typeof result.then === 'function') {
                throw new TypeError('extension registration must be synchronous');
            }
            return pending.commit();
        } catch (error) {
            pending.rollback();
            throw error;
        }
    }

    function markUnavailable(target, reason) {
        let types;
        if (typeof target === 'string' && ownerTypes.has(target)) {
            types = ownerTypes.get(target);
        } else if (typeof target === 'string') {
            types = [requireIdentifier(target, 'widget type')];
        } else if (target && typeof target !== 'string' && target[Symbol.iterator]) {
            types = normalizeExpectedTypes(target);
        } else {
            throw new TypeError('markUnavailable target must be a widget type, ownerId, or iterable of widget types');
        }
        const normalizedReason = errorReason(reason);
        types.forEach((type) => unavailableReasons.set(type, normalizedReason));
        return Object.freeze([...types]);
    }

    return Object.freeze({
        begin,
        transaction,
        registerOwner: transaction,
        has(type) {
            return registrations.has(type);
        },
        get(type) {
            return registrations.get(type) || null;
        },
        types() {
            return [...registrations.keys()];
        },
        ownerOf(type) {
            return typeOwners.get(type) || null;
        },
        markUnavailable,
        unavailable(type) {
            return unavailableReasons.get(type) || null;
        },
    });
}

export const componentRegistry = createComponentRegistry();
