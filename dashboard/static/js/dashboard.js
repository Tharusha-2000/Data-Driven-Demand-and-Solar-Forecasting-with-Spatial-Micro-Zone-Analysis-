/**
 * dashboard.js
 * Frontend logic for Nugegoda Electricity Demand Forecasting Dashboard.
 * Handles API calls, Chart.js rendering, and UI updates.
 */

// ── Globals & Chart Instances ──
let singleChartInstance = null;
let compareChartInstance = null;
let weeklyChartInstance = null;

// Chart.js Default Config (Dark Theme)
Chart.defaults.color = '#94a3b8';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.plugins.tooltip.backgroundColor = 'rgba(15, 15, 35, 0.9)';
Chart.defaults.plugins.tooltip.titleColor = '#f1f5f9';
Chart.defaults.plugins.tooltip.bodyColor = '#f1f5f9';
Chart.defaults.plugins.tooltip.borderColor = 'rgba(255, 255, 255, 0.1)';
Chart.defaults.plugins.tooltip.borderWidth = 1;
Chart.defaults.plugins.tooltip.padding = 10;
Chart.defaults.plugins.tooltip.displayColors = true;

// Custom Colors
const colors = {
    weekday: { border: '#00d4ff', bg: 'rgba(0, 212, 255, 0.15)' },
    weekend: { border: '#10b981', bg: 'rgba(16, 185, 129, 0.15)' },
    holiday: { border: '#f43f5e', bg: 'rgba(244, 63, 94, 0.15)' },
    weekly:  { border: '#8b5cf6', bg: 'rgba(139, 92, 246, 0.15)' }
};


// ── UI Helpers ──
function showLoading() { document.getElementById('loadingOverlay').classList.remove('hidden'); }
function hideLoading() { document.getElementById('loadingOverlay').classList.add('hidden'); }

function updateKpiCards(peak, peakTime, min, minTime, avg, scenarioName) {
    document.getElementById('kpi-peak-value').innerText = peak;
    document.getElementById('kpi-peak-time').innerText = peakTime;
    
    document.getElementById('kpi-min-value').innerText = min;
    document.getElementById('kpi-min-time').innerText = minTime;
    
    document.getElementById('kpi-avg-value').innerText = avg;
    
    document.getElementById('kpi-scenario-value').innerText = scenarioName;
    document.getElementById('kpi-scenario-detail').innerText = 'Active Model';
}


// ── 1. Single-Day Forecast ──
async function fetchSingleForecast() {
    const dateStr = document.getElementById('forecast-date').value;
    const scenario = document.getElementById('scenario-select').value;
    
    if (!dateStr) { alert("Please select a date."); return; }

    showLoading();
    try {
        const response = await fetch('/api/forecast/single', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: dateStr, scenario: scenario })
        });
        
        const data = await response.json();
        
        // Update KPIs
        updateKpiCards(
            data.peak_demand, `at ${data.peak_time}`,
            data.min_demand, `at ${data.min_time}`,
            data.avg_demand, data.scenario
        );

        // Update Chart Header
        document.getElementById('single-chart-badge').innerText = `${data.scenario} | ${dateStr}`;

        // Render Chart
        renderSingleChart(data, scenario);
        
        // Smooth scroll to chart
        document.getElementById('single-chart-section').scrollIntoView({ behavior: 'smooth' });

    } catch (error) {
        console.error("Error fetching single forecast:", error);
        alert("Failed to generate forecast.");
    } finally {
        hideLoading();
    }
}

function renderSingleChart(data, scenarioKey) {
    const ctx = document.getElementById('singleChart').getContext('2d');
    
    if (singleChartInstance) { singleChartInstance.destroy(); }

    const theme = colors[scenarioKey] || colors.weekday;
    
    // Labels (HH:MM)
    const labels = data.dates.map(d => d.split(' ')[1]);

    singleChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Predicted Demand (kW)',
                    data: data.yhat,
                    borderColor: theme.border,
                    backgroundColor: theme.bg,
                    borderWidth: 2,
                    pointBackgroundColor: theme.border,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    fill: true,
                    tension: 0.4
                },
                {
                    label: 'Upper 95% CI',
                    data: data.yhat_upper,
                    borderColor: 'transparent',
                    backgroundColor: 'transparent',
                    pointRadius: 0,
                    fill: false,
                    tooltip: false
                },
                {
                    label: 'Lower 95% CI',
                    data: data.yhat_lower,
                    borderColor: 'transparent',
                    backgroundColor: 'rgba(255,255,255,0.02)',
                    pointRadius: 0,
                    fill: '-1', // Fill to upper CI
                    tooltip: false
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.05)' } },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    title: { display: true, text: 'Demand (kW)' }
                }
            },
            plugins: {
                legend: { display: true, labels: { filter: item => item.text !== 'Upper 95% CI' && item.text !== 'Lower 95% CI' } },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            if (context.datasetIndex === 0) {
                                const lower = data.yhat_lower[context.dataIndex];
                                const upper = data.yhat_upper[context.dataIndex];
                                return `Demand: ${context.parsed.y} kW (${lower} - ${upper})`;
                            }
                            return null;
                        }
                    }
                }
            }
        }
    });
}


// ── 2. Compare All Scenarios ──
async function fetchComparison() {
    const dateStr = document.getElementById('forecast-date').value;
    
    if (!dateStr) { alert("Please select a date."); return; }

    showLoading();
    try {
        const response = await fetch('/api/forecast/compare', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: dateStr })
        });
        
        const data = await response.json();
        
        document.getElementById('compare-chart-badge').innerText = `Comparison for ${dateStr}`;

        renderCompareChart(data.scenarios);
        renderComparisonTable(data.scenarios);
        
        document.getElementById('comparison-table-wrapper').style.display = 'block';
        document.getElementById('compare-chart-section').scrollIntoView({ behavior: 'smooth' });

    } catch (error) {
        console.error("Error fetching comparison:", error);
        alert("Failed to generate comparison.");
    } finally {
        hideLoading();
    }
}

function renderCompareChart(scenarios) {
    const ctx = document.getElementById('compareChart').getContext('2d');
    
    if (compareChartInstance) { compareChartInstance.destroy(); }

    const labels = scenarios[0].dates.map(d => d.split(' ')[1]);
    
    const datasets = [
        {
            label: 'Weekday',
            data: scenarios[0].yhat,
            borderColor: colors.weekday.border,
            backgroundColor: colors.weekday.bg,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
            fill: true,
            tension: 0.4
        },
        {
            label: 'Weekend',
            data: scenarios[1].yhat,
            borderColor: colors.weekend.border,
            backgroundColor: colors.weekend.bg,
            borderWidth: 2,
            borderDash: [5, 5],
            pointRadius: 0,
            pointHoverRadius: 5,
            fill: true,
            tension: 0.4
        },
        {
            label: 'Holiday',
            data: scenarios[2].yhat,
            borderColor: colors.holiday.border,
            backgroundColor: 'transparent',
            borderWidth: 2,
            borderDash: [2, 2],
            pointRadius: 0,
            pointHoverRadius: 5,
            fill: false,
            tension: 0.4
        }
    ];

    compareChartInstance = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: { grid: { color: 'rgba(255, 255, 255, 0.05)' } },
                y: { grid: { color: 'rgba(255, 255, 255, 0.05)' }, title: { display: true, text: 'Demand (kW)' } }
            }
        }
    });
}

function renderComparisonTable(scenarios) {
    const tbody = document.getElementById('comparison-table-body');
    tbody.innerHTML = '';
    
    scenarios.forEach(s => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${s.scenario}</td>
            <td><strong>${s.peak_demand}</strong></td>
            <td>${s.peak_time}</td>
            <td>${s.min_demand}</td>
            <td>${s.avg_demand}</td>
        `;
        tbody.appendChild(tr);
    });
}


// ── 3. 7-Day Rolling Forecast ──
async function fetchWeeklyForecast() {
    const dateStr = document.getElementById('forecast-date').value;
    
    if (!dateStr) { alert("Please select a date."); return; }

    showLoading();
    try {
        const response = await fetch('/api/forecast/weekly', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: dateStr })
        });
        
        const data = await response.json();
        
        const endDate = data.dates[data.dates.length - 1].split(' ')[0];
        document.getElementById('weekly-chart-badge').innerText = `${dateStr} to ${endDate}`;

        renderWeeklyChart(data);
        renderDailyPeaksTable(data.daily_peaks);
        
        document.getElementById('daily-peaks-wrapper').style.display = 'block';
        document.getElementById('weekly-chart-section').scrollIntoView({ behavior: 'smooth' });

    } catch (error) {
        console.error("Error fetching weekly forecast:", error);
        alert("Failed to generate 7-day forecast.");
    } finally {
        hideLoading();
    }
}

function renderWeeklyChart(data) {
    const ctx = document.getElementById('weeklyChart').getContext('2d');
    
    if (weeklyChartInstance) { weeklyChartInstance.destroy(); }

    const labels = data.dates; // Full datetime strings

    weeklyChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Predicted Demand (kW)',
                    data: data.yhat,
                    borderColor: colors.weekly.border,
                    backgroundColor: colors.weekly.bg,
                    borderWidth: 1.5,
                    pointRadius: 0,
                    pointHoverRadius: 4,
                    fill: true,
                    tension: 0.2
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
                    ticks: {
                        maxTicksLimit: 14,
                        callback: function(val, index) {
                            // Show Date and Hour cleanly
                            const dt = labels[index];
                            if (dt.endsWith('12:00')) return dt.split(' ')[0];
                            return '';
                        }
                    }
                },
                y: { grid: { color: 'rgba(255, 255, 255, 0.05)' }, title: { display: true, text: 'Demand (kW)' } }
            }
        }
    });
}

function renderDailyPeaksTable(dailyPeaks) {
    const tbody = document.getElementById('daily-peaks-body');
    tbody.innerHTML = '';
    
    dailyPeaks.forEach(d => {
        const tr = document.createElement('tr');
        const typeBadge = d.is_weekend 
            ? `<span style="color:${colors.weekend.border};">Weekend</span>` 
            : `<span style="color:${colors.weekday.border};">Weekday</span>`;
            
        tr.innerHTML = `
            <td>${d.date}</td>
            <td>${d.day_name}</td>
            <td>${typeBadge}</td>
            <td><strong>${d.peak_demand}</strong></td>
            <td>${d.peak_time}</td>
            <td>${d.avg_demand}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ── Initialize on load ──
window.onload = () => {
    // Optionally auto-fetch single forecast on load
    // fetchSingleForecast();
};
