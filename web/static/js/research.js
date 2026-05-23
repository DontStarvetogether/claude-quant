const state = {
  presets: [],
  universes: [],
  currentRunId: null,
};

const $ = (id) => document.getElementById(id);

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function appendLog(message) {
  const log = $("progress-log");
  if (!log) return;
  const row = document.createElement("div");
  row.textContent = message;
  log.prepend(row);
}

function setProgress(status, progress, message) {
  setText("status-pill", status);
  const bar = $("progress-bar");
  if (bar) bar.style.width = `${Math.max(0, Math.min(100, progress || 0))}%`;
  if (message) appendLog(message);
}

async function fetchJson(url, options = {}) {
  const resp = await fetch(url, options);
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

async function loadPresets() {
  const payload = await fetchJson("/api/research/presets");
  state.presets = payload.factors || [];
  const select = $("factor-id");
  if (!select) return;
  select.innerHTML = state.presets
    .map((item) => `<option value="${item.id}">${item.name}</option>`)
    .join("");
  updateFactorDesc();
}

async function loadUniverses() {
  const payload = await fetchJson("/api/research/universes");
  state.universes = payload.universes || [];
  const select = $("universe-id");
  if (!select) return;
  select.innerHTML = state.universes
    .map((item) => `<option value="${item.id}">${item.name}</option>`)
    .join("");
  updateUniverseDesc();
}

function updateFactorDesc() {
  const selected = state.presets.find((item) => item.id === $("factor-id")?.value);
  setText("factor-desc", selected ? selected.description : "—");
}

function updateUniverseDesc() {
  const selected = state.universes.find((item) => item.id === $("universe-id")?.value);
  if (!selected) {
    setText("universe-desc", "—");
    return;
  }
  const quality = selected.quality || selected.construction || selected.source || "unknown";
  const count = selected.symbol_count ?? selected.symbols?.length ?? 0;
  setText("universe-desc", `${quality} · ${count} 只`);
}

function parsePeriods() {
  return ($("forward-periods")?.value || "1,5,20")
    .split(",")
    .map((item) => Number.parseInt(item.trim(), 10))
    .filter((item) => Number.isInteger(item) && item > 0);
}

function buildRequest() {
  return {
    factor_id: $("factor-id")?.value,
    universe_id: $("universe-id")?.value,
    price_source: $("price-source")?.value || "local_cache",
    price_csv: $("price-csv")?.value || null,
    adjust: "qfq",
    pit_csv: $("pit-csv")?.value || null,
    start_date: $("start-date")?.value,
    end_date: $("end-date")?.value,
    forward_periods: parsePeriods(),
    groups: Number.parseInt($("groups")?.value || "5", 10),
    ic_method: $("ic-method")?.value || "spearman",
    rebalance: $("rebalance")?.value || "weekly",
    sample_split_date: $("sample-split-date")?.value || null,
    winsorize: Boolean($("winsorize")?.checked),
    zscore: Boolean($("zscore")?.checked),
    neutralize: $("neutralize")?.value || "none",
  };
}

async function submitResearch(event) {
  event.preventDefault();
  const btn = $("submit-btn");
  if (btn) btn.disabled = true;
  setProgress("pending", 0, "提交任务");
  try {
    const payload = await fetchJson("/api/research/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildRequest()),
    });
    state.currentRunId = payload.run_id;
    setProgress(payload.status || "pending", 5, `任务 ${payload.run_id} 已创建`);
    watchRun(payload.run_id);
  } catch (err) {
    setProgress("failed", 0, err.message || String(err));
    if (btn) btn.disabled = false;
  }
}

function watchRun(runId) {
  const source = new EventSource(`/api/research/${runId}/stream`);
  source.addEventListener("progress", (event) => {
    const data = JSON.parse(event.data);
    setProgress("running", data.progress, data.message || data.current_step || "运行中");
  });
  source.addEventListener("completed", (event) => {
    source.close();
    const data = JSON.parse(event.data);
    window.location.href = data.redirect || `/research_result.html?run_id=${runId}`;
  });
  source.addEventListener("error", (event) => {
    source.close();
    let message = "任务失败";
    if (event.data) {
      try {
        message = JSON.parse(event.data).message || message;
      } catch {}
    }
    setProgress("failed", 100, message);
    const btn = $("submit-btn");
    if (btn) btn.disabled = false;
  });
}

async function loadHistory() {
  const target = $("history-list");
  if (!target) return;
  try {
    const payload = await fetchJson("/api/research/history/list");
    const runs = payload.runs || [];
    if (!runs.length) {
      target.textContent = "暂无研究记录";
      return;
    }
    target.innerHTML = runs.map((run) => `
      <a href="/research_result.html?run_id=${run.run_id}" class="block border border-gray-800 rounded-lg px-3 py-2 hover:border-blue-500 transition-colors">
        <div class="flex items-center justify-between gap-3">
          <span class="text-gray-200">${run.factor_name || run.factor_id}</span>
          <span class="text-xs text-gray-500">${run.status}</span>
        </div>
        <div class="text-xs text-gray-500 mt-1">${run.universe_id} · ${run.start_date} 至 ${run.end_date}</div>
      </a>
    `).join("");
  } catch (err) {
    target.textContent = err.message || String(err);
  }
}

async function init() {
  $("factor-id")?.addEventListener("change", updateFactorDesc);
  $("universe-id")?.addEventListener("change", updateUniverseDesc);
  $("research-form")?.addEventListener("submit", submitResearch);
  $("refresh-history-btn")?.addEventListener("click", loadHistory);
  try {
    await Promise.all([loadPresets(), loadUniverses(), loadHistory()]);
  } catch (err) {
    appendLog(err.message || String(err));
  }
}

init();
