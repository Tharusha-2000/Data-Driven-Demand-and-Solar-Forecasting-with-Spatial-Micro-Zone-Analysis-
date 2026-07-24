/**
 * dashboard.js
 * Frontend logic for Nugegoda Electricity Demand Forecasting Dashboard.
 * Updated to use the data-driven /api/forecast/single endpoint.
 * API now only needs { date } — day type and weather are auto-detected.
 */

// ── Globals & Chart Instances ──────────────────────────────
let singleChartInstance = null;
let compareChartInstance = null;
let overview7DayChartInstance = null;

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

            // ── Auto-run forecast, scenario comparison, and 7-day overview on load ──
            await fetchSingleForecast();
            await fetchCompareForecast();
            await fetch7DayOverview();
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

        // Update KPI cards
        updateKpiCards(
            data.peak_demand, data.peak_time,
            data.min_demand, data.min_time,
            data.avg_demand, data.scenario
        );

        // Update chart badge
        const dayName = new Date(dateStr + 'T00:00:00').toLocaleDateString('en-GB', { weekday: 'long' });
        document.getElementById('single-chart-badge').innerText =
            `${data.scenario}  ·  ${dateStr}  (${dayName})`;

        // Render forecast chart with temperature overlay
        renderForecastChart(data);

        // Render hourly table
        renderHourlyTable(data);

        // Scroll to chart
        document.getElementById('single-chart-section').scrollIntoView({ behavior: 'smooth' });

    } catch (error) {
        console.error('Error fetching forecast:', error);
        alert('Failed to generate forecast. Please check the server.');
    } finally {
        hideLoading();
    }
}


// ── Render Chart ───────────────────────────────────────────
function renderForecastChart(data) {
    const ctx = document.getElementById('singleChart').getContext('2d');
    if (singleChartInstance) singleChartInstance.destroy();

    const theme = scenarioColors[data.scenario] || defaultColor;
    const labels = data.dates.map(d => d.split(' ')[1]);

    // Temperature data from weather array
    const tempData = (data.weather || []).map(w => w.temperature);
    const hasTemp = tempData.some(t => t !== null && t !== undefined);

    const datasets = [
        // CI band (lower → upper fill)
        {
            label: 'Upper 95% CI',
            data: data.yhat_upper,
            borderColor: 'transparent',
            backgroundColor: 'transparent',
            pointRadius: 0,
            fill: false,
        },
        {
            label: 'Lower 95% CI',
            data: data.yhat_lower,
            borderColor: 'transparent',
            backgroundColor: 'rgba(255,255,255,0.03)',
            pointRadius: 0,
            fill: '-1',  // fill up to upper CI
        },
        // Main demand line
        {
            label: 'Predicted Demand (kW)',
            data: data.yhat,
            borderColor: theme.border,
            backgroundColor: theme.bg,
            borderWidth: 2.5,
            pointBackgroundColor: theme.border,
            pointRadius: 3,
            pointHoverRadius: 7,
            fill: true,
            tension: 0.4,
            yAxisID: 'y',
        },
    ];

    // Add temperature line on secondary y-axis if available
    if (hasTemp) {
        datasets.push({
            label: 'Temperature (°C)',
            data: tempData,
            borderColor: '#f59e0b',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            borderDash: [5, 4],
            pointRadius: 0,
            pointHoverRadius: 5,
            fill: false,
            tension: 0.4,
            yAxisID: 'yTemp',
        });
    }

    singleChartInstance = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { maxTicksLimit: 12 },
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Demand (kW)', color: '#94a3b8' },
                    position: 'left',
                },
                ...(hasTemp ? {
                    yTemp: {
                        grid: { drawOnChartArea: false },
                        title: { display: true, text: 'Temp (°C)', color: '#f59e0b' },
                        position: 'right',
                        ticks: { color: '#f59e0b' },
                    }
                } : {}),
            },
            plugins: {
                legend: {
                    display: true,
                    labels: {
                        filter: item =>
                            item.text !== 'Upper 95% CI' && item.text !== 'Lower 95% CI',
                    },
                },
                tooltip: {
                    callbacks: {
                        label(context) {
                            if (context.datasetIndex === 2) {
                                const i = context.dataIndex;
                                const lower = data.yhat_lower[i];
                                const upper = data.yhat_upper[i];
                                return `Demand: ${context.parsed.y} kW  (CI: ${lower} – ${upper})`;
                            }
                            if (context.dataset.label === 'Temperature (°C)') {
                                return `Temp: ${context.parsed.y} °C`;
                            }
                            return null;
                        },
                    },
                },
            },
        },
    });
}


// ── Render Hourly Table ────────────────────────────────────
function renderHourlyTable(data) {
    const tbody = document.getElementById('hourly-table-body');
    const section = document.getElementById('hourly-table-section');
    const badge = document.getElementById('hourly-table-badge');

    tbody.innerHTML = '';
    badge.innerText = `${data.date}  ·  ${data.scenario}`;

    const weather = data.weather || [];

    data.dates.forEach((dt, i) => {
        const time = dt.split(' ')[1];
        const yhat = data.yhat[i];
        const lower = data.yhat_lower[i];
        const upper = data.yhat_upper[i];

        // Highlight peak hour
        const isPeak = yhat === data.peak_demand;
        const tr = document.createElement('tr');
        if (isPeak) tr.style.background = 'rgba(0,212,255,0.07)';

        tr.innerHTML = `
            <td><strong>${time}</strong>${isPeak ? ' <span style="color:#00d4ff;font-size:0.75rem;">PEAK</span>' : ''}</td>
            <td><strong style="color:#f1f5f9;">${yhat}</strong></td>
            <td style="color:var(--text-muted);">${lower}</td>
            <td style="color:var(--text-muted);">${upper}</td>
        `;
        tbody.appendChild(tr);
    });

    section.style.display = 'block';
}


// ── Scenario Comparison: Fetch & Render ─────────────────────
async function fetchCompareForecast() {
    const dateStr = document.getElementById('forecast-date').value;
    if (!dateStr) { alert('Please select a date.'); return; }

    showLoading();
    try {
        const response = await fetch('/api/forecast/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: dateStr }),
        });

        if (!response.ok) {
            alert('Failed to load scenario comparison.');
            return;
        }

        const data = await response.json();
        const scenarios = data.scenarios || [];

        // Update badge
        document.getElementById('compare-chart-badge').innerText = '3 Scenarios';

        // Render multi-scenario chart & summary cards
        renderCompareChart(scenarios, dateStr);
        renderCompareCards(scenarios);

        // Show compare section
        const section = document.getElementById('compare-chart-section');
        section.style.display = 'block';

    } catch (error) {
        console.error('Error fetching scenario comparison:', error);
        alert('Failed to generate scenario comparison. Please check the server.');
    } finally {
        hideLoading();
    }
}


function renderCompareChart(scenarios, dateStr) {
    const ctx = document.getElementById('compareChart').getContext('2d');
    if (compareChartInstance) compareChartInstance.destroy();

    // Use time labels from the first scenario
    const firstSc = scenarios[0] || {};
    const labels = (firstSc.dates || []).map(d => d.split(' ')[1]);

    const datasets = scenarios.map(sc => {
        const theme = scenarioColors[sc.scenario] || defaultColor;
        return {
            label: `${sc.scenario} Demand (kW)`,
            data: sc.yhat,
            borderColor: theme.border,
            backgroundColor: theme.bg,
            borderWidth: 2.5,
            pointBackgroundColor: theme.border,
            pointRadius: 3,
            pointHoverRadius: 7,
            fill: false,
            tension: 0.4,
        };
    });

    compareChartInstance = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { maxTicksLimit: 12 },
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Demand (kW)', color: '#94a3b8' },
                    position: 'left',
                },
            },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#f1f5f9', font: { weight: '600' } },
                },
                tooltip: {
                    callbacks: {
                        label(context) {
                            return `${context.dataset.label}: ${context.parsed.y} kW`;
                        },
                    },
                },
            },
        },
    });
}


function renderCompareCards(scenarios) {
    const grid = document.getElementById('compare-cards-grid');
    grid.innerHTML = '';

    scenarios.forEach(sc => {
        const theme = scenarioColors[sc.scenario] || defaultColor;
        const card = document.createElement('div');
        card.className = 'kpi-card glass-card';
        card.style.borderColor = theme.border;
        card.style.background = 'rgba(255, 255, 255, 0.03)';
        card.style.padding = '1.25rem';

        card.innerHTML = `
            <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:0.75rem;">
                <span style="font-weight:700; font-size:1.05rem; color:${theme.border};">${sc.scenario}</span>
                <span style="font-size:0.75rem; background:${theme.bg}; color:${theme.border}; padding:0.2rem 0.6rem; border-radius:12px; border:1px solid ${theme.border};">Scenario</span>
            </div>
            <div style="display:flex; flex-direction:column; gap:0.4rem; font-size:0.88rem;">
                <div style="display:flex; justify-content:space-between;">
                    <span style="color:var(--text-secondary);">Peak Demand:</span>
                    <strong style="color:#f1f5f9;">${sc.peak_demand} kW <small style="color:var(--text-muted);">(${sc.peak_time})</small></strong>
                </div>
                <div style="display:flex; justify-content:space-between;">
                    <span style="color:var(--text-secondary);">Min Demand:</span>
                    <strong style="color:#f1f5f9;">${sc.min_demand} kW <small style="color:var(--text-muted);">(${sc.min_time})</small></strong>
                </div>
                <div style="display:flex; justify-content:space-between;">
                    <span style="color:var(--text-secondary);">Avg Demand:</span>
                    <strong style="color:var(--accent-blue);">${sc.avg_demand} kW</strong>
                </div>
            </div>
        `;
        grid.appendChild(card);
    });
}


// ── Overview Dashboard: 7-Day Demand Forecast ───────────────
async function fetch7DayOverview() {
    try {
        const response = await fetch('/api/forecast/7day');
        if (!response.ok) return;

        const data = await response.json();
        const daily = data.daily || [];

        // 1. Console Log Each Day's Full Demand
        console.log("==================================================================");
        console.log("⚡ OVERVIEW DASHBOARD — 7-DAY ELECTRICITY DEMAND FORECAST");
        console.log(`TOTAL 7-DAY DEMAND: ${data.total_7day_demand_kwh} kWh`);
        console.log("==================================================================");
        daily.forEach((d, i) => {
            console.log(`[Day ${i+1}] ${d.date} (${d.day_name} · ${d.day_type}):`);
            console.log(`   └─ Full Daily Total Demand : ${d.total_demand_kwh} kWh`);
            console.log(`   └─ Peak Demand            : ${d.peak_demand_kw} kW at ${d.peak_time}`);
            console.log(`   └─ Min Demand             : ${d.min_demand_kw} kW at ${d.min_time}`);
            console.log(`   └─ Avg Demand             : ${d.avg_demand_kw} kW`);
        });
        console.log("==================================================================");

        // 2. Update KPI Summary Cards
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

        // 3. Render 7-Day Chart (Bar + Line)
        render7DayChart(daily);

        // 4. Render Daily Table
        render7DayTable(daily);

    } catch (e) {
        console.error('Error fetching 7-day overview forecast:', e);
    }
}


function render7DayChart(daily) {
    const ctx = document.getElementById('overview7DayChart');
    if (!ctx) return;
    if (overview7DayChartInstance) overview7DayChartInstance.destroy();

    const labels = daily.map(d => `${d.date.slice(5)} (${d.day_name.slice(0, 3)})`);
    const totals = daily.map(d => d.total_demand_kwh);

    overview7DayChartInstance = new Chart(ctx.getContext('2d'), {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Total Daily Demand (kWh)',
                    data: totals,
                    backgroundColor: 'rgba(0, 212, 255, 0.45)',
                    borderColor: '#00d4ff',
                    borderWidth: 2,
                    borderRadius: 8,
                    hoverBackgroundColor: 'rgba(0, 212, 255, 0.7)',
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
                    beginAtZero: false,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    title: { display: true, text: 'Total Demand (kWh)', color: '#00d4ff' }
                }
            },
            plugins: {
                legend: { display: true, position: 'top' },
                tooltip: {
                    callbacks: {
                        label(ctx) {
                            return `Total Demand: ${ctx.parsed.y.toLocaleString()} kWh`;
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


// ── Initialize on page load ────────────────────────────────
window.onload = () => {
    initDashboard();
};


