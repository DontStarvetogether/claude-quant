const params = new URLSearchParams(window.location.search);
const runId = params.get("run_id");

const $ = (id) => document.getElementById(id);

function pct(value, digits = 2) {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "—";
}

function num(value, digits = 4) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : "—";
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

async function fetchJson(url) {
  const resp = await fetch(url);
  if (!resp.ok) {
    let message = `${resp.status} ${resp.statusText}`;
    try {
      const payload = await resp.json();
      message = payload.detail || payload.message || message;
    } catch {}
    throw new Error(message);
  }
  return resp.json();
}

function table(rows, columns) {
  if (!rows?.length) return '<div class="text-gray-500">暂无数据</div>';
  return `
    <table class="w-full text-left">
      <thead class="text-xs text-gray-500 border-b border-gray-800">
        <tr>${columns.map((c) => `<th class="py-2 pr-3">${c.label}</th>`).join("")}</tr>
      </thead>
      <tbody class="divide-y divide-gray-800">
        ${rows.map((row) => `
          <tr>${columns.map((c) => `<td class="py-2 pr-3 text-gray-300">${c.format ? c.format(row[c.key], row) : (row[c.key] ?? "—")}</td>`).join("")}</tr>
        `).join("")}
      </tbody>
    </table>
  `;
}

function renderSummary(data) {
  const ic = data.tables?.ic_summary?.[0] || {};
  const coverageRows = data.tables?.coverage || [];
  const coverageAvg = coverageRows.length
    ? coverageRows.reduce((sum, row) => sum + Number(row.coverage || 0), 0) / coverageRows.length
    : NaN;
  const topRows = data.tables?.top_bottom_return || [];
  const topAvg = topRows.length
    ? topRows.reduce((sum, row) => sum + Number(row.top_bottom_return || 0), 0) / topRows.length
    : NaN;

  setText("header-info", `${data.factor_name} · ${data.universe_id} · ${data.start_date} 至 ${data.end_date}`);
  setText("m-coverage", pct(coverageAvg));
  setText("m-ic-mean", num(ic.ic_mean));
  setText("m-icir", num(ic.icir, 3));
  setText("m-top-bottom", pct(topAvg));
}

function renderTables(data) {
  $("ic-summary-table").innerHTML = table(data.tables?.ic_summary || [], [
    { key: "period", label: "周期", format: (v) => `${v}D` },
    { key: "ic_mean", label: "IC Mean", format: num },
    { key: "ic_std", label: "IC Std", format: num },
    { key: "icir", label: "ICIR", format: (v) => num(v, 3) },
    { key: "ic_win_rate", label: "胜率", format: pct },
    { key: "count", label: "样本数" },
  ]);
  $("monotonicity-table").innerHTML = table(data.tables?.monotonicity || [], [
    { key: "period", label: "周期", format: (v) => `${v}D` },
    { key: "mean_group_rank_corr", label: "组序相关", format: num },
    { key: "monotonic_ratio", label: "正单调比例", format: pct },
    { key: "count", label: "样本数" },
  ]);
}

function renderCharts(data) {
  if (!window.echarts) return;
  renderIcChart(data.tables?.ic || []);
  renderGroupNavChart(data.tables?.group_nav || []);
  renderTopBottomChart(data.tables?.top_bottom_return || []);
  renderQualityChart(data.tables?.coverage || [], data.tables?.turnover_by_group || []);
}

function chartBase(id) {
  const el = $(id);
  return el ? echarts.init(el) : null;
}

function renderIcChart(rows) {
  const chart = chartBase("ic-chart");
  if (!chart) return;
  const periods = [...new Set(rows.map((r) => r.period))].sort((a, b) => a - b);
  chart.setOption({
    backgroundColor: "transparent",
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#9ca3af" } },
    xAxis: { type: "category", data: [...new Set(rows.map((r) => r.date))], axisLabel: { color: "#9ca3af" } },
    yAxis: { type: "value", axisLabel: { color: "#9ca3af" }, splitLine: { lineStyle: { color: "#1f2937" } } },
    series: periods.map((period) => ({
      name: `${period}D`,
      type: "line",
      showSymbol: false,
      data: rows.filter((r) => r.period === period).map((r) => [r.date, r.ic]),
    })),
  });
}

function renderGroupNavChart(rows) {
  const chart = chartBase("group-nav-chart");
  if (!chart) return;
  const firstPeriod = [...new Set(rows.map((r) => r.period))].sort((a, b) => a - b)[0];
  const filtered = rows.filter((r) => r.period === firstPeriod);
  const groups = [...new Set(filtered.map((r) => r.group))].sort((a, b) => a - b);
  chart.setOption({
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#9ca3af" } },
    xAxis: { type: "category", data: [...new Set(filtered.map((r) => r.date))], axisLabel: { color: "#9ca3af" } },
    yAxis: { type: "value", axisLabel: { color: "#9ca3af" }, splitLine: { lineStyle: { color: "#1f2937" } } },
    series: groups.map((group) => ({
      name: `G${group}`,
      type: "line",
      showSymbol: false,
      data: filtered.filter((r) => r.group === group).map((r) => [r.date, r.nav]),
    })),
  });
}

function renderTopBottomChart(rows) {
  const chart = chartBase("top-bottom-chart");
  if (!chart) return;
  const firstPeriod = [...new Set(rows.map((r) => r.period))].sort((a, b) => a - b)[0];
  const filtered = rows.filter((r) => r.period === firstPeriod);
  let nav = 1;
  chart.setOption({
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: filtered.map((r) => r.date), axisLabel: { color: "#9ca3af" } },
    yAxis: { type: "value", axisLabel: { color: "#9ca3af" }, splitLine: { lineStyle: { color: "#1f2937" } } },
    series: [{
      name: "Top-Bottom NAV",
      type: "line",
      showSymbol: false,
      data: filtered.map((r) => {
        nav *= 1 + Number(r.top_bottom_return || 0);
        return [r.date, nav];
      }),
    }],
  });
}

function renderQualityChart(coverage, turnover) {
  const chart = chartBase("quality-chart");
  if (!chart) return;
  const turnoverByDate = new Map();
  for (const row of turnover) {
    const value = Number(row.turnover);
    if (!Number.isFinite(value)) continue;
    if (!turnoverByDate.has(row.date)) turnoverByDate.set(row.date, []);
    turnoverByDate.get(row.date).push(value);
  }
  chart.setOption({
    tooltip: { trigger: "axis" },
    legend: { textStyle: { color: "#9ca3af" } },
    xAxis: { type: "category", data: coverage.map((r) => r.date), axisLabel: { color: "#9ca3af" } },
    yAxis: { type: "value", axisLabel: { color: "#9ca3af", formatter: (v) => `${Math.round(v * 100)}%` }, splitLine: { lineStyle: { color: "#1f2937" } } },
    series: [
      { name: "覆盖率", type: "line", showSymbol: false, data: coverage.map((r) => [r.date, r.coverage]) },
      {
        name: "平均换手",
        type: "line",
        showSymbol: false,
        data: coverage.map((r) => {
          const values = turnoverByDate.get(r.date) || [];
          const avg = values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
          return [r.date, avg];
        }),
      },
    ],
  });
}

function renderDiagnostics(data) {
  const diagnostics = $("diagnostics");
  const items = [
    ["状态", data.status],
    ["数据质量", data.diagnostics?.data_quality || "unknown"],
    ["前视风险", data.diagnostics?.lookahead_risk || "checked"],
    ["样本切分", data.diagnostics?.sample_diagnostics?.status || "unavailable"],
  ];
  diagnostics.innerHTML = items.map(([label, value]) => `
    <div class="flex justify-between border-b border-gray-800 py-2">
      <span class="text-gray-500">${label}</span>
      <span class="text-gray-300">${typeof value === "object" ? JSON.stringify(value) : value}</span>
    </div>
  `).join("");

  const artifacts = data.artifacts || {};
  $("artifact-list").innerHTML = Object.entries(artifacts).map(([name, url]) => `
    <a href="${url}" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 rounded text-xs text-gray-300 transition-colors">${name}</a>
  `).join("");
}

function setupBenchmarkButton(data) {
  const btn = $("benchmark-btn");
  if (!btn) return;
  btn.addEventListener("click", () => {
    const q = new URLSearchParams({
      factor_id: data.factor_id || data.factor_name,
      universe_id: data.universe_id,
      top_n: "20",
      rebalance: data.request?.rebalance || "weekly",
      research_run_id: data.run_id,
    });
    window.location.href = `/benchmark.html?${q.toString()}`;
  });
}

async function init() {
  if (!runId) throw new Error("缺少 run_id");
  const data = await fetchJson(`/api/research/${runId}/result`);
  renderSummary(data);
  renderTables(data);
  renderCharts(data);
  renderDiagnostics(data);
  setupBenchmarkButton(data);
  $("loading").classList.add("hidden");
  $("main-content").classList.remove("hidden");
}

init().catch((err) => {
  const loading = $("loading");
  if (loading) loading.innerHTML = `<div class="text-red-400 text-sm">${err.message || String(err)}</div>`;
});
