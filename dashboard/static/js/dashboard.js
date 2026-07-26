/**
 * dashboard.js
 * Frontend logic for Nugegoda Electricity Demand & Solar Generation Forecasting Dashboard.
 */

// ── Globals & Chart Instances ──────────────────────────────
let singleChartInstance = null;
let compareChartInstance = null;
let overview7DayChartInstance = null;
let solarForecastChartInstance = null;
let solarDailyChartInstance = null;

let cachedSolarData = null;
let cachedDemandData = null;

// ── Chart.js Default Config (Dark Theme) ──────────────────
Chart.defaults.color = '#94a3b8';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(15, 15, 35, 0.92)';
Chart.defaults.plugins.tooltip.titleColor = '#f1f5f9';
Chart.defaults.plugins.tooltip.bodyColor = '#cbd5e1';
Chart.defaults.plugins.tooltip.borderColor = 'rgba(255, 255, 255, 0.1)';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.padding = 12;
Chart.defaults.plugins.tooltip.displayColors = true;

// ── Scenario colour palette ────────────────────────────────
const scenarioColors = {
    'Weekday': { border: '#00d4ff', bg: 'rgba(0, 212, 255, 0.12)' },
    'Weekend': { border: '#10b981', bg: 'rgba(16, 185, 129, 0.12)' },
    'Public Holiday': { border: '#f43f5e', bg: 'rgba(244, 63, 94, 0.12)' },
};
const defaultColor = { border: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.12)' };


// ── UI Helpers ─────────────────────────────────────────────
function showLoading() { document.getElementById('loadingOverlay').classList.remove('hidden'); }
function hideLoading() { document.getElementById('loadingOverlay').classList.add('hidden'); }

function updateKpiCards(peak, peakTime, min, minTime, avg, dayType) {
    document.getElementById('kpi-peak-value').innerText = `${peak} kW`;
    document.getElementById('kpi-peak-time').innerText = `at ${peakTime}`;
    document.getElementById('kpi-min-value').innerText = `${min} kW`;
    document.getElementById('kpi-min-time').innerText = `at ${minTime}`;
    document.getElementById('kpi-avg-value').innerText = `${avg} kW`;
    document.getElementById('kpi-scenario-value').innerText = dayType;
    document.getElementById('kpi-scenario-detail').innerText = 'Auto-detected from calendar';
}

// ── Initialise: set date picker to today, load available dates ─
async function initDashboard() {
    const today = new Date().toISOString().split('T')[0];
    const datePicker = document.getElementById('forecast-date');

    try {
        const res = await fetch('/api/forecast-input');
        const rows = await res.json();

        if (Array.isArray(rows) && rows.length) {
            const dates = [...new Set(rows.map(r => r.ds.slice(0, 10)))].sort();
            const firstDate = dates[0];

            // Set picker bounds and value to first available date
            datePicker.min = firstDate;
            datePicker.max = dates[dates.length - 1];
            datePicker.value = dates.includes(today) ? today : firstDate;

            const hint = document.getElementById('available-dates-hint');
            hint.innerHTML = `📅 Available: <strong>${firstDate}</strong> → <strong>${dates[dates.length - 1]}</strong> (${dates.length} days)`;

            // ── Auto-run forecast, scenario comparison, 7-day overview & solar forecast on load ──
            await fetchSingleForecast();
            await fetchCompareForecast();
            await fetch7DayOverview();
            await fetchSolarForecast();
        }
    } catch (e) {
        console.warn('Could not load available dates:', e);
        datePicker.value = today;
    }
}


// ── Main: Fetch 24-Hour Forecast ───────────────────────────
async function fetchSingleForecast() {
    const dateStr = document.getElementById('forecast-date').value;
    if (!dateStr) { alert('Please select a date.'); return; }

    showLoading();
    try {
        const response = await fetch('/api/forecast/single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: dateStr }),
        });

        if (!response.ok) {
            const err = await response.json();
            const available = (err.available_dates || []).join(', ');
            alert(`Date not available.\n\nAvailable dates:\n${available}`);
            return;
        }

        const data = await response.json();

        // 1. Update KPI Cards
        updateKpiCards(
            data.peak_demand,
            data.peak_time,
            data.min_demand,
            data.min_time,
            data.avg_demand,
            data.scenario
        );

        // 2. Render Single Line Chart
        renderSingleChart(data);

        // 3. Render Hourly Breakdown Table
        renderHourlyTable(data);

    } catch (e) {
        console.error('Error fetching single forecast:', e);
        alert('Failed to connect to the server. Is app.py running?');
    } finally {
        hideLoading();
    }
}


// ── Render 24-Hour Single Forecast Chart ───────────────────
function renderSingleChart(data) {
    const ctx = document.getElementById('singleChart').getContext('2d');
    if (singleChartInstance) singleChartInstance.destroy();

    const hours = data.dates.map(d => d.slice(11, 16));
    const pal = scenarioColors[data.scenario] || defaultColor;

    // Calculate dynamic Y bounds
    const allY = [...data.yhat, ...data.yhat_upper, ...data.yhat_lower];
    const minY = Math.floor(Math.min(...allY) * 0.95);
    const maxY = Math.ceil(Math.max(...allY) * 1.05);

    document.getElementById('single-chart-title').innerText =
        `24-Hour Demand Forecast — ${data.date} (${data.scenario})`;

    const sourceLabel = data.data_source === 'forecast_input_7days.json'
        ? 'Live Weather' : 'Feature Matrix';
    document.getElementById('single-chart-badge').innerText = `${data.scenario} · ${sourceLabel}`;

    singleChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: hours,
            datasets: [
                {
                    label: 'Predicted Load (kW)',
                    data: data.yhat,
                    borderColor: pal.border,
                    backgroundColor: pal.bg,
                    borderWidth: 3,
                    fill: true,
                    tension: 0.4,
                    pointRadius: 4,
                    pointBackgroundColor: pal.border,
                    pointHoverRadius: 7,
                    zIndex: 10,
                },
                {
                    label: 'Upper Bound (95% CI)',
                    data: data.yhat_upper,
                    borderColor: 'rgba(255, 255, 255, 0.2)',
                    borderWidth: 1,
                    borderDash: [4, 4],
                    fill: false,
                    pointRadius: 0,
                },
                {
                    label: 'Lower Bound (95% CI)',
                    data: data.yhat_lower,
                    borderColor: 'rgba(255, 255, 255, 0.2)',
                    borderWidth: 1,
                    borderDash: [4, 4],
                    fill: '-1',
                    backgroundColor: 'rgba(255, 255, 255, 0.03)',
                    pointRadius: 0,
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Hour of Day (Asia/Colombo)', color: '#64748b' }
                },
                y: {
                    min: minY,
                    max: maxY,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Demand (kW)', color: pal.border }
                }
            },
            plugins: {
                legend: { display: true, position: 'top' },
                tooltip: {
                    callbacks: {
                        label(ctx) {
                            return `${ctx.dataset.label}: ${ctx.parsed.y} kW`;
                        }
                    }
                }
            }
        }
    });
}


// ── Render Hourly Breakdown Table ──────────────────────────
function renderHourlyTable(data) {
    const tableSection = document.getElementById('hourly-table-section');
    const tbody = document.getElementById('hourly-table-body');
    const badge = document.getElementById('hourly-table-badge');

    tbody.innerHTML = '';
    badge.innerText = `${data.date} · 24 Hours`;

    data.dates.forEach((dateTimeStr, idx) => {
        const timeStr = dateTimeStr.slice(11, 16);
        const yhat = data.yhat[idx];
        const lower = data.yhat_lower[idx];
        const upper = data.yhat_upper[idx];

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${timeStr}</strong></td>
            <td><strong style="color:var(--accent-primary);">${yhat} kW</strong></td>
            <td style="color:var(--text-muted);">${lower} kW</td>
            <td style="color:var(--text-muted);">${upper} kW</td>
        `;
        tbody.appendChild(tr);
    });

    tableSection.style.display = 'block';
}


// ── Scenario Comparison (Weekday vs Weekend vs Holiday) ───
async function fetchCompareForecast() {
    const dateStr = document.getElementById('forecast-date').value;
    if (!dateStr) return;

    showLoading();
    try {
        const response = await fetch('/api/forecast/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: dateStr }),
        });

        if (!response.ok) return;

        const data = await response.json();
        renderCompareChart(data);
        renderCompareCards(data.scenarios);

    } catch (e) {
        console.error('Error fetching scenario comparison:', e);
    } finally {
        hideLoading();
    }
}


function renderCompareChart(data) {
    const ctx = document.getElementById('compareChart').getContext('2d');
    if (compareChartInstance) compareChartInstance.destroy();

    document.getElementById('compare-chart-title').innerText =
        `Scenario Comparison — Weekday vs Weekend vs Holiday (${data.date})`;

    const hours = data.scenarios[0].dates.map(d => d.slice(11, 16));

    const datasets = data.scenarios.map(sc => {
        const pal = scenarioColors[sc.scenario] || defaultColor;
        return {
            label: sc.scenario,
            data: sc.yhat,
            borderColor: pal.border,
            backgroundColor: pal.bg,
            borderWidth: 2.5,
            fill: false,
            tension: 0.4,
            pointRadius: 3,
            pointHoverRadius: 6,
        };
    });

    compareChartInstance = new Chart(ctx, {
        type: 'line',
        data: { labels: hours, datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Hour of Day', color: '#64748b' }
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Demand (kW)', color: '#f1f5f9' }
                }
            },
            plugins: {
                legend: { display: true, position: 'top' },
            }
        }
    });
}


function renderCompareCards(scenarios) {
    const wrapper = document.getElementById('compare-cards-grid');
    if (!wrapper) return;
    wrapper.innerHTML = '';

    scenarios.forEach(sc => {
        const pal = scenarioColors[sc.scenario] || defaultColor;
        const card = document.createElement('div');
        card.className = 'glass-card';
        card.style.borderLeft = `4px solid ${pal.border}`;

        card.innerHTML = `
            <h4 style="color:${pal.border}; font-size:1.05rem; font-weight:700; margin-bottom:0.75rem;">${sc.scenario}</h4>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:0.5rem; font-size:0.85rem;">
                <div><span style="color:var(--text-muted);">Peak Load:</span> <strong style="color:var(--text-primary);">${sc.peak_demand} kW</strong></div>
                <div><span style="color:var(--text-muted);">Peak Time:</span> <strong style="color:var(--text-primary);">${sc.peak_time}</strong></div>
                <div><span style="color:var(--text-muted);">Min Load:</span> <strong style="color:var(--text-primary);">${sc.min_demand} kW</strong></div>
                <div><span style="color:var(--text-muted);">Avg Load:</span> <strong style="color:var(--text-primary);">${sc.avg_demand} kW</strong></div>
            </div>
        `;
        wrapper.appendChild(card);
    });
}


// ── Overview Dashboard: 7-Day Demand Forecast ───────────────
async function fetch7DayOverview() {
    try {
        const response = await fetch('/api/forecast/7day');
        if (!response.ok) return;

        const data = await response.json();
        const daily = data.daily || [];
        cachedDemandData = data;

        // Update KPI Summary Cards
        document.getElementById('overview-7day-total').innerText = `${data.total_7day_demand_kwh.toLocaleString()} kWh`;
        const avgDaily = (data.total_7day_demand_kwh / (daily.length || 7)).toFixed(2);
        document.getElementById('overview-daily-avg').innerText = `${Number(avgDaily).toLocaleString()} kWh`;

        if (daily.length) {
            const sortedByTotal = [...daily].sort((a, b) => b.total_demand_kwh - a.total_demand_kwh);
            const maxDay = sortedByTotal[0];
            const minDay = sortedByTotal[sortedByTotal.length - 1];

            document.getElementById('overview-max-day').innerText = `${maxDay.total_demand_kwh.toLocaleString()} kWh`;
            document.getElementById('overview-max-day-detail').innerText = `${maxDay.date} (${maxDay.day_name})`;

            document.getElementById('overview-min-day').innerText = `${minDay.total_demand_kwh.toLocaleString()} kWh`;
            document.getElementById('overview-min-day-detail').innerText = `${minDay.date} (${minDay.day_name})`;
        }

        // Build solar daily map from cached data or fetch it
        const solarDailyMap = {};
        if (!cachedSolarData) {
            try {
                const solarRes = await fetch('/api/solar/forecast');
                if (solarRes.ok) cachedSolarData = await solarRes.json();
            } catch (err) {
                console.warn('Could not fetch solar forecast for overview:', err);
            }
        }
        if (cachedSolarData && cachedSolarData.daily) {
            cachedSolarData.daily.forEach(sd => {
                solarDailyMap[sd.date] = sd.total_export_kwh;
            });
        }

        // Render 7-Day Demand Chart & Table
        render7DayChart(daily, solarDailyMap);
        render7DayTable(daily);

    } catch (e) {
        console.error('Error fetching 7-day overview forecast:', e);
    }
}


function render7DayChart(daily, solarDailyMap) {
    const ctx = document.getElementById('overview7DayChart');
    if (!ctx) return;
    if (overview7DayChartInstance) overview7DayChartInstance.destroy();

    const labels = daily.map(d => `${d.date.slice(5)} (${d.day_name.slice(0, 3)})`);
    const demandData = daily.map(d => d.total_demand_kwh);
    const solarData = daily.map(d => solarDailyMap[d.date] || 0);
    const netGridData = daily.map((d, i) => Number(Math.max(0, d.total_demand_kwh - solarData[i]).toFixed(2)));

    overview7DayChartInstance = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Total Daily Demand (kWh)',
                    data: demandData,
                    backgroundColor: 'rgba(0, 212, 255, 0.45)',
                    borderColor: '#00d4ff',
                    borderWidth: 2,
                    borderRadius: 6,
                    order: 2
                },
                {
                    label: 'Solar Export Generation (kWh)',
                    data: solarData,
                    backgroundColor: 'rgba(245, 158, 11, 0.65)',
                    borderColor: '#f59e0b',
                    borderWidth: 2,
                    borderRadius: 6,
                    order: 2
                },
                {
                    type: 'line',
                    label: 'Net External Grid Needed (kWh)',
                    data: netGridData,
                    borderColor: '#a855f7',
                    backgroundColor: 'rgba(168, 85, 247, 0.15)',
                    borderWidth: 3,
                    pointRadius: 5,
                    pointBackgroundColor: '#a855f7',
                    tension: 0.25,
                    order: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
                y: {
                    type: 'linear',
                    beginAtZero: true,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Energy (kWh)', color: '#f1f5f9' }
                }
            },
            plugins: {
                legend: { display: true, position: 'top' },
                tooltip: {
                    callbacks: {
                        label(ctx) {
                            return `${ctx.dataset.label}: ${ctx.parsed.y.toLocaleString()} kWh`;
                        }
                    }
                }
            }
        }
    });
}


function render7DayTable(daily) {
    const tbody = document.getElementById('overview-7day-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    daily.forEach(d => {
        const tr = document.createElement('tr');
        const badgeColor = d.day_type === 'Weekend' ? '#10b981' : (d.day_type === 'Public Holiday' ? '#f43f5e' : '#00d4ff');

        tr.innerHTML = `
            <td><strong>${d.date}</strong> <span style="color:var(--text-muted);font-size:0.8rem;">(${d.day_name})</span></td>
            <td><span style="font-size:0.75rem; background:rgba(255,255,255,0.05); color:${badgeColor}; padding:0.2rem 0.6rem; border-radius:10px; border:1px solid ${badgeColor};">${d.day_type}</span></td>
            <td><strong style="color:#00d4ff;">${d.total_demand_kwh.toLocaleString()} kWh</strong></td>
            <td><strong style="color:#f1f5f9;">${d.peak_demand_kw} kW</strong></td>
            <td style="color:var(--text-muted);">${d.peak_time}</td>
            <td style="color:var(--text-muted);">${d.min_demand_kw} kW (${d.min_time})</td>
            <td style="color:var(--text-secondary);">${d.avg_demand_kw} kW</td>
        `;
        tbody.appendChild(tr);
    });
}


// ══════════════════════════════════════════════════════════
// SOLAR GENERATION FORECAST FUNCTIONS
// ══════════════════════════════════════════════════════════
async function fetchSolarForecast() {
    try {
        const res = await fetch('/api/solar/forecast');
        if (!res.ok) {
            console.error('Failed to fetch solar forecast:', await res.text());
            return;
        }
        const data = await res.json();
        if (data.status !== 'success') {
            console.error('Solar forecast error:', data.message);
            return;
        }

        cachedSolarData = data;

        // 1. Update KPI Cards
        document.getElementById('solar-kpi-total').innerText = `${Number(data.total_7day_export_kwh).toLocaleString()} kWh`;
        document.getElementById('solar-kpi-peak').innerText = `${data.peak_export_kw} kW`;
        document.getElementById('solar-kpi-peak-time').innerText = `at ${data.peak_time}`;
        document.getElementById('solar-kpi-avg').innerText = `${Number(data.avg_daily_export_kwh).toLocaleString()} kWh`;
        document.getElementById('solar-kpi-ghi').innerText = `${data.max_ghi} W/m²`;

        // 2. Render Charts
        renderSolarForecastChart(data.hourly);
        renderSolarDailyChart(data.daily);

        // 3. Render Table
        renderSolarTable(data.hourly);

    } catch (e) {
        console.error('Error fetching solar forecast:', e);
    }
}


function renderSolarForecastChart(hourly) {
    const ctx = document.getElementById('solarForecastChart');
    if (!ctx) return;
    if (solarForecastChartInstance) solarForecastChartInstance.destroy();

    const labels = hourly.map(h => {
        const parts = h.ds.split(' ');
        const dateStr = parts[0].slice(5); // MM-DD
        return h.time === '00:00' || h.time === '12:00' ? `${dateStr} ${h.time}` : h.time;
    });

    const exportData = hourly.map(h => h.predicted_export_kw);
    const ghiData = hourly.map(h => h.ghi);

    solarForecastChartInstance = new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Predicted Solar Export (kW)',
                    data: exportData,
                    borderColor: '#f59e0b',
                    backgroundColor: 'rgba(245, 158, 11, 0.18)',
                    borderWidth: 2.5,
                    fill: true,
                    tension: 0.35,
                    pointRadius: 0,
                    pointHoverRadius: 5,
                    yAxisID: 'y1'
                },
                {
                    label: 'Solar Radiation GHI (W/m²)',
                    data: ghiData,
                    borderColor: '#06b6d4',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    yAxisID: 'y2'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 14 }
                },
                y1: {
                    type: 'linear',
                    display: true,
                    position: 'left',
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    title: { display: true, text: 'Export Rate (kW)', color: '#f59e0b' }
                },
                y2: {
                    type: 'linear',
                    display: true,
                    position: 'right',
                    beginAtZero: true,
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: 'Solar Radiation (W/m²)', color: '#06b6d4' }
                }
            },
            plugins: {
                legend: { display: true, position: 'top' },
                tooltip: {
                    callbacks: {
                        label(ctx) {
                            if (ctx.dataset.yAxisID === 'y1') {
                                return `Predicted Export: ${ctx.parsed.y} kW`;
                            }
                            return `Solar Radiation: ${ctx.parsed.y} W/m²`;
                        }
                    }
                }
            }
        }
    });
}


function renderSolarDailyChart(solarDaily) {
    const ctx = document.getElementById('solarDailyChart');
    if (!ctx) return;
    if (solarDailyChartInstance) solarDailyChartInstance.destroy();

    const labels = solarDaily.map(d => `${d.date.slice(5)} (${d.day_name.slice(0, 3)})`);
    const totals = solarDaily.map(d => d.total_export_kwh);

    solarDailyChartInstance = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Daily Solar Export (kWh)',
                    data: totals,
                    backgroundColor: 'rgba(245, 158, 11, 0.55)',
                    borderColor: '#f59e0b',
                    borderWidth: 2,
                    borderRadius: 8,
                    hoverBackgroundColor: 'rgba(245, 158, 11, 0.85)',
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                },
                y: {
                    type: 'linear',
                    beginAtZero: true,
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    title: { display: true, text: 'Total Solar Export (kWh)', color: '#f59e0b' }
                }
            },
            plugins: {
                legend: { display: true, position: 'top' },
                tooltip: {
                    callbacks: {
                        label(ctx) {
                            return `Solar Export: ${ctx.parsed.y.toLocaleString()} kWh`;
                        }
                    }
                }
            }
        }
    });
}


function renderSolarTable(hourly) {
    const tbody = document.getElementById('solar-table-body');
    if (!tbody) return;
    tbody.innerHTML = '';

    hourly.forEach(h => {
        const tr = document.createElement('tr');
        const isNight = h.ghi === 0;
        const exportColor = isNight ? 'var(--text-muted)' : '#f59e0b';

        tr.innerHTML = `
            <td><strong>${h.date}</strong></td>
            <td>${h.time}</td>
            <td><strong style="color:${exportColor};">${h.predicted_export_kw} kW</strong></td>
            <td style="color:${isNight ? 'var(--text-muted)' : '#06b6d4'};">${h.ghi} W/m²</td>
            <td style="color:var(--text-secondary);">${h.temp} °C</td>
            <td style="color:var(--text-secondary);">${h.cloud} %</td>
        `;
        tbody.appendChild(tr);
    });
}


// ── Initialize on page load ────────────────────────────────
window.onload = () => {
    initDashboard();
};
