import { fetchOffPeakBadge } from './api.js';
import { state } from './state.js';

const HOLIDAYS = {
    '2025-01-01': '元旦', '2025-01-28': '春节', '2025-01-29': '春节', '2025-01-30': '春节',
    '2025-01-31': '春节', '2025-02-01': '春节', '2025-02-02': '春节', '2025-02-03': '春节',
    '2025-02-04': '春节', '2025-04-04': '清明节', '2025-04-05': '清明节', '2025-04-06': '清明节',
    '2025-05-01': '劳动节', '2025-05-02': '劳动节', '2025-05-03': '劳动节', '2025-05-04': '劳动节',
    '2025-05-05': '劳动节', '2025-05-31': '端午节', '2025-06-01': '端午节', '2025-06-02': '端午节',
    '2025-10-01': '国庆节', '2025-10-02': '国庆节', '2025-10-03': '国庆节', '2025-10-04': '国庆节',
    '2025-10-05': '国庆节', '2025-10-06': '国庆节', '2025-10-07': '国庆节',
    '2026-01-01': '元旦', '2026-01-02': '元旦', '2026-01-03': '元旦', '2026-02-17': '春节',
    '2026-02-18': '春节', '2026-02-19': '春节', '2026-02-20': '春节', '2026-02-21': '春节',
    '2026-02-22': '春节', '2026-02-23': '春节', '2026-04-05': '清明节', '2026-04-06': '清明节',
    '2026-04-07': '清明节', '2026-05-01': '劳动节', '2026-05-02': '劳动节', '2026-05-03': '劳动节',
    '2026-05-04': '劳动节', '2026-05-05': '劳动节', '2026-06-19': '端午节', '2026-06-20': '端午节',
    '2026-06-21': '端午节', '2026-10-01': '国庆节', '2026-10-02': '国庆节', '2026-10-03': '国庆节',
    '2026-10-04': '国庆节', '2026-10-05': '国庆节', '2026-10-06': '国庆节', '2026-10-07': '国庆节',
};

const WORKDAYS = {
    '2025-01-26': 1, '2025-02-08': 1, '2025-04-27': 1, '2025-09-28': 1, '2025-10-11': 1,
    '2026-02-15': 1, '2026-02-28': 1, '2026-04-26': 1, '2026-10-10': 1,
};

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

function dateKey(date) {
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
}

function getDayType(date) {
    const key = dateKey(date);
    const day = date.getDay();
    if (HOLIDAYS[key]) return { type: 'holiday', label: HOLIDAYS[key] };
    if (WORKDAYS[key]) return { type: 'workday', label: WEEKDAYS[day] };
    if (day === 0 || day === 6) return { type: 'weekend', label: WEEKDAYS[day] };
    return { type: 'workday', label: WEEKDAYS[day] };
}

function timeToMinutes(value) {
    const match = typeof value === 'string' && /^(?:[01]\d|2[0-3]):[0-5]\d$/.exec(value);
    return match ? Number(value.slice(0, 2)) * 60 + Number(value.slice(3, 5)) : null;
}

function isInOffPeakRange(minuteOfDay, ranges) {
    if (!Array.isArray(ranges)) return false;
    return ranges.some((range) => {
        const start = timeToMinutes(range?.start);
        const end = timeToMinutes(range?.end);
        if (start === null || end === null || start === end) return false;
        return start < end
            ? minuteOfDay >= start && minuteOfDay < end
            : minuteOfDay >= start || minuteOfDay < end;
    });
}

export function tickClock() {
    const date = new Date();
    const hours = date.getHours();
    const hours12 = hours % 12 || 12;
    document.getElementById('hdrClock').textContent = `${String(hours12).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`;
    document.getElementById('hdrAmpm').textContent = hours < 12 ? 'AM' : 'PM';
    const dayInfo = getDayType(date);
    let dateText = `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日 ${WEEKDAYS[date.getDay()]}`;
    if (dayInfo.type === 'holiday') dateText += ` · ${dayInfo.label}`;
    document.getElementById('hdrDate').textContent = dateText;
    const rate = document.getElementById('hdrRate');
    const config = state.clock.offPeakBadgeConfig;
    if (!config.enabled) {
        rate.style.display = 'none';
        return;
    }
    const beijingMinutes = ((date.getUTCHours() + 8) % 24) * 60 + date.getUTCMinutes();
    rate.textContent = isInOffPeakRange(beijingMinutes, config.ranges) ? '0.8x' : '1.0x';
    rate.style.display = 'inline';
}

export async function refreshOffPeakBadgeConfig() {
    try {
        const data = await fetchOffPeakBadge();
        if (!data || typeof data !== 'object') return;
        state.clock.offPeakBadgeConfig = {
            enabled: data.enabled !== false,
            ranges: Array.isArray(data.ranges) ? data.ranges : [],
        };
        tickClock();
    } catch (error) {
        console.warn('[off-peak] config refresh failed:', error);
    }
}

export function startClock() {
    tickClock();
    refreshOffPeakBadgeConfig();
    state.timers.clock = setInterval(tickClock, 1000);
    state.timers.offPeak = setInterval(refreshOffPeakBadgeConfig, 60000);
}
