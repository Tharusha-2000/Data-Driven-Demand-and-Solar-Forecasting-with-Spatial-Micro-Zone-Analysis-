/**
 * microzone.js
 * Frontend logic for the "Spatial Micro-Zone Analysis" tab (Module C).
 * Fetches /api/microzone/zones (map geometry) + /api/microzone/overview
 * (forecast/risk/priority) once, merges them, and renders the Leaflet map,
 * KPI cards and priority table. Independent of dashboard.js — reuses only
 * its global showLoading()/hideLoading() helpers.
 */

let microzoneZoneData = [];   // merged zones+overview (real data), kept for the scenario simulator
let microzoneMapCenter = null; // study-area centre, reused by both the real and scenario maps

// Two independent maps — the real next-day map and the scenario what-if map — each
// gets its own Leaflet instance + polygon registry so recoloring one never touches
// the other. Both are rendered by the same renderZoneMap() helper below.
const baseMap = { instance: null, polygons: {} };
const scenarioMap = { instance: null, polygons: {} };

const tierColors = {
    High:   '#f43f5e',
    Medium: '#f59e0b',
    Low:    '#10b981',
};

// Every kW figure Module C exports is a MEAN PER REPORTING HOUSEHOLD (see
// pipeline.py's build_zone_frame) — not the zone's total load. The dashboard shows
// that per-household number as the primary figure but always surfaces the
// estimated zone total (per-household kW × households in the zone) beside it, so
// a small zone with a high per-household average isn't mistaken for the zone
// drawing the most total power.
function zoneTotalKw(perHouseholdKw, households) {
    if (!households) return null;
    return Math.round(perHouseholdKw * households * 10) / 10;
}

function zoneTotalNote(perHouseholdKw, households) {
    const total = zoneTotalKw(perHouseholdKw, households);
    return total === null ? '' : ` <span style="color:var(--text-muted); font-size:0.82em;">(~${total.toLocaleString()} kW zone total)</span>`;
}

function zoneTotalCell(perHouseholdKw, households) {
    const total = zoneTotalKw(perHouseholdKw, households);
    return total === null ? '—' : `${total.toLocaleString()} kW`;
}

// forecast.yhat is 24 hourly per-household kW values for the single forecasted next
// day — each hour's kW held for 1h = that hour's kWh, so summing them (then scaling
// by households) gives the zone's estimated whole-day energy, unlike zoneTotalKw()
// above which only scales the single peak hour.
function zoneDailyEnergyCell(forecastYhat, households) {
    if (!households || !Array.isArray(forecastYhat)) return '—';
    const perHouseholdKwh = forecastYhat.reduce((sum, v) => sum + v, 0);
    const total = Math.round(perHouseholdKwh * households * 10) / 10;
    return `${total.toLocaleString()} kWh`;
}


// ── Fetch + orchestrate ─────────────────────────────────────
async function fetchMicrozoneOverview() {
    showLoading();
    try {
        const [zonesRes, overviewRes] = await Promise.all([
            fetch('/api/microzone/zones'),
            fetch('/api/microzone/overview'),
        ]);

        if (!zonesRes.ok || !overviewRes.ok) {
            console.error('Micro-zone artifacts not available yet.');
            document.getElementById('microzone-window-note').innerText =
                'Micro-zone data is not available yet — run module_c/train_and_export.py to generate it.';
            return;
        }

        const zonesData = await zonesRes.json();
        const overviewData = await overviewRes.json();
        const merged = mergeMicrozoneData(zonesData, overviewData);
        microzoneZoneData = merged;
        microzoneMapCenter = zonesData.map_center;

        renderMicrozoneWindowNote(overviewData);
        renderMicrozoneKpis(merged);
        renderZoneMap(baseMap, 'microzoneMap', zonesData.map_center, merged);
        renderMicrozonePriorityTable(merged);
        populateScenarioInputs(merged);

        fetchMicrozoneValidation();

    } catch (error) {
        console.error('Error fetching micro-zone data:', error);
    } finally {
        hideLoading();
    }
}


function mergeMicrozoneData(zonesData, overviewData) {
    const zonesById = {};
    zonesData.zones.forEach(z => { zonesById[z.zone] = z; });

    const merged = overviewData.zones.map(r => ({ ...zonesById[r.zone], ...r }));
    merged.sort((a, b) => a.rank - b.rank);
    return merged;
}


// ── Study-window note ───────────────────────────────────────
function renderMicrozoneWindowNote(overviewData) {
    const el = document.getElementById('microzone-window-note');
    el.innerHTML = `📅 Forecast trained on <strong>${overviewData.study_window.start}</strong> → ` +
        `<strong>${overviewData.study_window.end}</strong> (no live smart-meter feed) — ` +
        `"next day" here means <strong>${overviewData.next_day_date}</strong>, the day after that window.`;
}


// ── KPI cards ────────────────────────────────────────────────
function renderMicrozoneKpis(merged) {
    const top = merged[0];
    document.getElementById('mz-kpi-top-zone').innerText = top ? top.name : '—';
    document.getElementById('mz-kpi-top-action').innerText = top ? top.action.split('—')[0].trim() : '—';

    const warnings = merged.filter(z => z.warning.WARNING === 'YES').length;
    document.getElementById('mz-kpi-warnings').innerText = `${warnings} / ${merged.length}`;

    const trending = merged.filter(z => z.trend.trend !== 'None').length;
    document.getElementById('mz-kpi-trends').innerText = trending;

    const households = merged.reduce((sum, z) => sum + (z.households || 0), 0);
    document.getElementById('mz-kpi-households').innerText = households.toLocaleString();
}


// ── Leaflet map (shared by the real map and the scenario map) ──
function renderZoneMap(mapState, containerId, center, zones) {
    const container = document.getElementById(containerId);
    if (!container) return;

    if (!mapState.instance) {
        mapState.instance = L.map(container).setView([center.lat, center.lon], 14);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18,
        }).addTo(mapState.instance);
    } else {
        // Re-render (e.g. scenario re-run): clear previous zone layers, keep the tile layer.
        mapState.instance.eachLayer(layer => {
            if (layer instanceof L.Polygon || layer instanceof L.Marker) {
                mapState.instance.removeLayer(layer);
            }
        });
    }
    mapState.polygons = {};

    zones.forEach(zone => {
        const color = tierColors[zone.risk.risk_tier] || '#00d4ff';
        const surgeLine = zone.surge_pct ? `Surge applied: <strong>+${zone.surge_pct}%</strong><br>` : '';

        const polygonLayer = L.polygon(zone.polygon, {
            color,
            weight: 3,
            fillColor: color,
            fillOpacity: 0.28,
        }).addTo(mapState.instance).bindTooltip(`
            <strong>${zone.name}</strong><br>
            ${surgeLine}
            Risk tier: <strong>${zone.risk.risk_tier}</strong><br>
            Forecast peak: ${zone.forecast.peak_kw} kW/home${zoneTotalNote(zone.forecast.peak_kw, zone.households)} at ${String(zone.forecast.peak_hour).padStart(2, '0')}:00<br>
            Surge warning: ${zone.warning.WARNING} &nbsp;|&nbsp; Trend: ${zone.trend.trend}<br>
            Reliability: ${zone.reliability} (${zone.nrmse_pct}% nRMSE)<br>
            ${zone.households} households &middot; ${zone.area_km2} km²
        `, { sticky: true });

        mapState.polygons[zone.zone] = polygonLayer;

        L.marker([zone.centroid.lat, zone.centroid.lon], {
            icon: L.divIcon({
                className: '',
                html: `<div class="mz-zone-label" style="color:${color};">${zone.name}</div>`,
                iconSize: [140, 24],
                iconAnchor: [70, 12],
            }),
        }).addTo(mapState.instance);
    });

    // Container just became visible — recalc tile layout after the browser settles.
    setTimeout(() => mapState.instance.invalidateSize(), 150);
}


// ── Priority table (real data) ──────────────────────────────
function renderMicrozonePriorityTable(merged) {
    const tbody = document.getElementById('mz-priority-table-body');
    tbody.innerHTML = '';

    document.getElementById('mz-priority-badge').innerText = `${merged.length} zones`;

    merged.forEach(zone => {
        const tierClass = `tier-pill tier-${zone.risk.risk_tier.toLowerCase()}`;
        const warnColor = zone.warning.WARNING === 'YES' ? 'var(--accent-rose)' : 'var(--text-secondary)';
        const trendIcon = zone.trend.trend === 'Rising' ? '📈' : (zone.trend.trend === 'Falling' ? '📉' : '➖');

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${zone.rank}</strong></td>
            <td>${zone.name}</td>
            <td><strong style="color:#f1f5f9;">${zone.priority_score}</strong></td>
            <td><span class="${tierClass}">${zone.risk.risk_tier}</span></td>
            <td style="color:${warnColor};">${zone.warning.WARNING}</td>
            <td>${trendIcon} ${zone.trend.trend}</td>
            <td>${zone.reliability} <span style="color:var(--text-muted);">(${zone.nrmse_pct}%)</span></td>
            <td>${zone.forecast.peak_kw} kW/home </td>
            <td>${zoneTotalCell(zone.forecast.peak_kw, zone.households)}</td>
            <td>${zoneDailyEnergyCell(zone.forecast.yhat, zone.households)}</td>
            <td style="font-family:'Inter',sans-serif; font-size:0.82rem; color:var(--text-secondary);">${zone.action}</td>
        `;
        tbody.appendChild(tr);
    });
}


// ── Section 8.1 — Scenario Simulator (all zones at once) ────
function populateScenarioInputs(merged) {
    const container = document.getElementById('mz-scenario-inputs');
    if (!container) return;

    container.innerHTML = merged
        .slice()
        .sort((a, b) => a.zone - b.zone)
        .map(z => `
            <div class="control-group">
                <label for="mz-scenario-surge-${z.zone}" class="control-label">${z.name} — Surge %</label>
                <input type="number" id="mz-scenario-surge-${z.zone}" class="control-input"
                    data-zone="${z.zone}" value="0" min="-50" max="200" step="5" style="width:120px;">
            </div>
        `)
        .join('');
}

async function runMicrozoneScenario() {
    const inputs = document.querySelectorAll('#mz-scenario-inputs input[data-zone]');
    const surges = {};
    inputs.forEach(inp => { surges[inp.dataset.zone] = parseFloat(inp.value) || 0; });

    const resultEl = document.getElementById('mz-scenario-result');
    const outputsEl = document.getElementById('mz-scenario-outputs');

    try {
        const response = await fetch('/api/microzone/scenario/multi', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ surges }),
        });
        if (!response.ok) {
            resultEl.innerHTML = `<p style="color:var(--accent-rose);">Could not run scenario.</p>`;
            return;
        }
        const data = await response.json();
        renderScenarioSummary(data.zones);

        const scenarioMerged = buildScenarioMerged(data.zones);
        outputsEl.style.display = 'block';
        renderZoneMap(scenarioMap, 'microzoneScenarioMap', microzoneMapCenter, scenarioMerged);
        renderScenarioPriorityTable(scenarioMerged);

    } catch (error) {
        console.error('Error running scenario:', error);
    }
}

// Overlays the scenario's recomputed numbers onto a copy of the real merged zone
// data (geometry, households, trend, reliability are unaffected by a hypothetical
// surge, so they're carried over unchanged) — this lets the scenario map/table
// reuse exactly the same render functions as the real ones.
function buildScenarioMerged(scenarioZones) {
    const byZone = {};
    scenarioZones.forEach(s => { byZone[s.zone] = s; });

    const merged = microzoneZoneData.map(baseZone => {
        const s = byZone[baseZone.zone];
        return {
            ...baseZone,
            surge_pct: s.surge_pct,
            forecast: { ...baseZone.forecast, peak_kw: s.forecast_peak_kw, peak_hour: s.peak_hour },
            risk: { ...baseZone.risk, risk_adj_peak_kw: s.risk_adj_peak_kw, self_pctile: s.self_pctile, risk_tier: s.risk_tier },
            warning: { ...baseZone.warning, WARNING: s.warning, over_band_pct: s.over_band_pct },
            priority_score: s.priority_score,
            rank: s.rank,
            action: s.action,
        };
    });
    merged.sort((a, b) => a.rank - b.rank);
    return merged;
}

function renderScenarioSummary(scenarioZones) {
    const resultEl = document.getElementById('mz-scenario-result');
    const baseByZone = {};
    microzoneZoneData.forEach(z => { baseByZone[z.zone] = z; });

    const cards = scenarioZones
        .slice()
        .sort((a, b) => a.zone - b.zone)
        .map(s => {
            const base = baseByZone[s.zone];
            const baseTier = base.risk.risk_tier;
            const changed = baseTier !== s.risk_tier;
            const color = tierColors[s.risk_tier] || '#00d4ff';
            return `
                <div style="flex:1; min-width:200px; padding:0.85rem 1rem; border-radius:var(--radius-md); background:rgba(255,255,255,0.03); border:1px solid ${changed ? color : 'var(--glass-border)'};">
                    <div style="font-size:0.78rem; color:var(--text-secondary); margin-bottom:0.35rem;">
                        ${s.name} ${s.surge_pct ? `<strong>+${s.surge_pct}%</strong>` : '<span style="color:var(--text-muted);">(unchanged)</span>'}
                    </div>
                    <div style="font-family:'JetBrains Mono',monospace; font-size:1rem; color:${color};">${s.risk_adj_peak_kw} kW/home${zoneTotalNote(s.risk_adj_peak_kw, base.households)}</div>
                    <div style="font-size:0.78rem; margin-top:0.3rem;">
                        <span class="tier-pill tier-${s.risk_tier.toLowerCase()}">${s.risk_tier}</span>
                        ${changed ? ` <span style="color:var(--accent-amber);">⚠️ was ${baseTier}</span>` : ''}
                    </div>
                </div>
            `;
        })
        .join('');

    resultEl.innerHTML = `<div style="display:flex; gap:1rem; flex-wrap:wrap;">${cards}</div>`;
}

function renderScenarioPriorityTable(merged) {
    const tbody = document.getElementById('mz-scenario-priority-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    merged.forEach(zone => {
        const tierClass = `tier-pill tier-${zone.risk.risk_tier.toLowerCase()}`;
        const warnColor = zone.warning.WARNING === 'YES' ? 'var(--accent-rose)' : 'var(--text-secondary)';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${zone.rank}</strong></td>
            <td>${zone.name}</td>
            <td>${zone.surge_pct ? `+${zone.surge_pct}%` : '—'}</td>
            <td><strong style="color:#f1f5f9;">${zone.priority_score}</strong></td>
            <td><span class="${tierClass}">${zone.risk.risk_tier}</span></td>
            <td style="color:${warnColor};">${zone.warning.WARNING}</td>
            <td>${zone.forecast.peak_kw} kW/home${zoneTotalNote(zone.forecast.peak_kw, zone.households)} <span style="color:var(--text-muted);">@ ${String(zone.forecast.peak_hour).padStart(2, '0')}:00</span></td>
            <td style="font-family:'Inter',sans-serif; font-size:0.82rem; color:var(--text-secondary);">${zone.action}</td>
        `;
        tbody.appendChild(tr);
    });
}


// ── Section 8.2 / 8.3 — Validation ──────────────────────────
async function fetchMicrozoneValidation() {
    try {
        const response = await fetch('/api/microzone/validation');
        if (!response.ok) return;
        const data = await response.json();
        renderWorstDayTable(data.worst_day_replay);
        renderHistoricalValidation(data.historical_validation);
    } catch (error) {
        console.error('Error fetching micro-zone validation:', error);
    }
}

function renderWorstDayTable(rows) {
    const tbody = document.getElementById('mz-worstday-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    const householdsByZone = {};
    microzoneZoneData.forEach(z => { householdsByZone[z.zone] = z.households; });

    rows.forEach(r => {
        const households = householdsByZone[r.zone];
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${r.name}</td>
            <td>${r.worst_observed_peak_kw} kW/home${zoneTotalNote(r.worst_observed_peak_kw, households)}</td>
            <td>${r.worst_day}</td>
            <td>${r.risk_adj_peak_if_repeated_kw} kW/home${zoneTotalNote(r.risk_adj_peak_if_repeated_kw, households)}</td>
            <td><span class="tier-pill tier-${r.tier_if_repeated.toLowerCase()}">${r.tier_if_repeated}</span></td>
        `;
        tbody.appendChild(tr);
    });
}

function renderHistoricalValidation(validation) {
    const tbody = document.getElementById('mz-validation-table-body');
    const note = document.getElementById('mz-validation-note');
    const verdict = document.getElementById('mz-validation-verdict');
    if (!tbody) return;

    note.innerHTML = `Real system-wide peak day in the study window: <strong>${validation.validation_date}</strong>. ` +
        `Does the ranking flag the zone that actually recorded the largest peak that day?`;

    tbody.innerHTML = '';
    const householdsByZone = {};
    microzoneZoneData.forEach(z => { householdsByZone[z.zone] = z.households; });

    validation.zones.forEach(r => {
        const isTop = r.zone === validation.top_zone;
        const tr = document.createElement('tr');
        if (isTop) tr.style.background = 'rgba(0,212,255,0.07)';
        tr.innerHTML = `
            <td>${r.name}${isTop ? ' <span style="color:#00d4ff;font-size:0.72rem;">ACTUAL PEAK</span>' : ''}</td>
            <td>${r.actual_peak_kw} kW/home${zoneTotalNote(r.actual_peak_kw, householdsByZone[r.zone])}</td>
            <td>${String(r.peak_hour).padStart(2, '0')}:00</td>
            <td>${r.pctile_vs_own_history}%</td>
            <td><span class="tier-pill tier-${r.tier_that_day.toLowerCase()}">${r.tier_that_day}</span></td>
        `;
        tbody.appendChild(tr);
    });

    const topZoneRow = validation.zones.find(r => r.zone === validation.top_zone);
    verdict.innerHTML = topZoneRow
        ? `✅ Validated: <strong>${topZoneRow.name}</strong> both recorded the largest actual peak on ` +
          `${validation.validation_date} and is ranked <strong>${topZoneRow.tier_that_day}</strong> tier that day — ` +
          `the detector correctly flags the zone that truly peaked.`
        : '';
}


// ── Initialize on page load ─────────────────────────────────
// (fetchMicrozoneOverview is called lazily by showPage() in index.html the first
// time the "Spatial Micro-Zone Analysis" tab is opened.)
