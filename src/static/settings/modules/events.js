import {$} from './dom.js';
import {state, setDirty} from './state.js';
import {handleSecretClick, updateSecretAction} from './secrets.js';
import {addMapRow, addObjectRow} from './schema/provider-panels.js';
import {addBalance, addOffPeakRange, addVram} from './render.js';
import {bindFontEvents} from './fonts.js';
import {refreshCaptureDevices} from './music-settings.js';
import {loadSettings, saveSettings} from './controller.js';

function bindDelegatedFormActions() {
    document.addEventListener('click', (event) => {
        const target = event.target.closest ? event.target.closest('button') : event.target;
        if (!target) return;
        if (target.dataset.revealFor || target.dataset.clearFor) {
            event.preventDefault();
            handleSecretClick(target);
            return;
        }
        if (target.dataset.removeRow) {
            const row = target.closest('.repeat-row');
            if (row) row.remove();
            setDirty(true);
            return;
        }
        if (target.dataset.addObject) {
            const objectField = target.closest('.provider-field');
            const list = $('.provider-object-rows', objectField);
            addObjectRow(list, target._objectSpec, {}, target._objectPath);
            setDirty(true);
            return;
        }
        if (target.dataset.addMap) {
            const mapField = target.closest('.provider-field');
            const list = $('.provider-map-rows', mapField);
            addMapRow(list, '', '', target._mapSpec);
            setDirty(true);
        }
    });
}

export function bindSettingsEvents() {
    bindDelegatedFormActions();
    const form = $('#settingsForm');
    form.addEventListener('input', (event) => {
        updateSecretAction(event.target);
        setDirty(true);
    });
    form.addEventListener('change', () => setDirty(true));

    $('#addOffPeakRange').addEventListener('click', addOffPeakRange);
    $('#addBalance').addEventListener('click', addBalance);
    $('#addVram').addEventListener('click', addVram);
    $('#saveButton').addEventListener('click', saveSettings);
    $('#reloadButton').addEventListener('click', loadSettings);
    const refreshDevices = $('#musicRefreshDevices');
    if (refreshDevices) {
        refreshDevices.addEventListener('click', () => refreshCaptureDevices(true).catch(() => {}));
    }
    bindFontEvents();

    window.addEventListener('beforeunload', (event) => {
        if (state.dirty) {
            event.preventDefault();
            event.returnValue = '';
        }
    });
}
