/**
 * 主页交互逻辑
 */

// ── 状态 ────────────────────────────────────────────────────────────────────
let strategies = [];
let selectedSymbols = new Set();
let currentRunId = null;
let sseSource = null;

// ── 初始化 ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  await loadStrategies();
  setupSymbolInput();
  setupRiskSliders();
  setupQuickSymbols();
  setupForm();
  loadHistory();
});

// ── 策略加载 ─────────────────────────────────────────────────────────────────
async function loadStrategies() {
  try {
    const data = await API.getStrategies();
    strategies = data.strategies;

    const select = document.getElementById('strategy-select');
    select.innerHTML = strategies.map(s =>
      `<option value="${s.id}">${s.name}</option>`
    ).join('');

    select.addEventListener('change', () => renderStrategyParams(select.value));
    renderStrategyParams(strategies[0]?.id);
  } catch (e) {
    console.error('加载策略失败', e);
  }
}

function renderStrategyParams(strategyId) {
  const strategy = strategies.find(s => s.id === strategyId);
  const container = document.getElementById('strategy-params');
  const descEl = document.getElementById('strategy-desc');

  if (!strategy) {
    container.innerHTML = '<p class="text-sm text-gray-500">请选择策略</p>';
    return;
  }

  descEl.textContent = strategy.description;

  if (!strategy.params.length) {
    container.innerHTML = '<p class="text-sm text-gray-500">该策略无可调参数</p>';
    return;
  }

  container.innerHTML = strategy.params.map(p => `
    <div>
      <label class="flex justify-between text-sm text-gray-400 mb-1">
        <span>${p.label}</span>
        <span id="param-${p.name}-label">${p.default}</span>
      </label>
      ${p.type === 'int' || p.type === 'float' ? `
        <input
          id="param-${p.name}"
          data-param="${p.name}"
          data-type="${p.type}"
          type="range"
          min="${p.min ?? 1}"
          max="${p.max ?? 100}"
          step="${p.step ?? 1}"
          value="${p.default}"
          class="w-full accent-blue-500"
        />
      ` : `
        <input id="param-${p.name}" data-param="${p.name}" type="text" value="${p.default}"
          class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 focus:outline-none focus:border-blue-500" />
      `}
    </div>
  `).join('');

  // 绑定 range label 同步
  container.querySelectorAll('input[type="range"]').forEach(input => {
    const labelEl = document.getElementById(`param-${input.dataset.param}-label`);
    input.addEventListener('input', () => { labelEl.textContent = input.value; });
  });
}

function getStrategyParams() {
  const params = {};
  document.querySelectorAll('[data-param]').forEach(el => {
    const name = el.dataset.param;
    const type = el.dataset.type;
    const raw = el.value;
    params[name] = type === 'int' ? parseInt(raw) : type === 'float' ? parseFloat(raw) : raw;
  });
  return params;
}

// ── 股票代码输入 ─────────────────────────────────────────────────────────────
function setupSymbolInput() {
  const input = document.getElementById('symbol-input');
  const btn = document.getElementById('add-symbol-btn');

  btn.addEventListener('click', () => addSymbol(input.value.trim().toUpperCase()));
  input.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); addSymbol(input.value.trim().toUpperCase()); }
  });
}

function addSymbol(symbol) {
  if (!symbol) return;
  if (!symbol.match(/^\d{6}\.(SH|SZ|BJ)$/i)) {
    showError(`代码格式不正确：${symbol}（期望如 600519.SH）`);
    return;
  }
  symbol = symbol.toUpperCase();
  selectedSymbols.add(symbol);
  renderSymbolTags();
  document.getElementById('symbol-input').value = '';
}

function removeSymbol(symbol) {
  selectedSymbols.delete(symbol);
  renderSymbolTags();
}

function renderSymbolTags() {
  const container = document.getElementById('symbol-tags');
  if (selectedSymbols.size === 0) {
    container.innerHTML = '';
    return;
  }
  container.innerHTML = [...selectedSymbols].map(sym => `
    <span class="inline-flex items-center gap-1 px-3 py-1 bg-blue-900/40 border border-blue-700/50 rounded-full text-xs text-blue-300">
      ${sym}
      <button onclick="removeSymbol('${sym}')" class="text-blue-500 hover:text-red-400 ml-1">×</button>
    </span>
  `).join('');
}

function setupQuickSymbols() {
  document.querySelectorAll('.quick-symbol').forEach(btn => {
    btn.addEventListener('click', () => {
      const sym = btn.dataset.symbol;
      if (selectedSymbols.has(sym)) {
        selectedSymbols.delete(sym);
        btn.classList.remove('border-blue-500', 'bg-blue-900/20');
      } else {
        selectedSymbols.add(sym);
        btn.classList.add('border-blue-500', 'bg-blue-900/20');
      }
      renderSymbolTags();
    });
  });
}

// ── 风控滑块 ─────────────────────────────────────────────────────────────────
function setupRiskSliders() {
  const maxPos = document.getElementById('max-pos-pct');
  const minCash = document.getElementById('min-cash-reserve');
  maxPos.addEventListener('input', () => {
    document.getElementById('max-pos-label').textContent = maxPos.value + '%';
  });
  minCash.addEventListener('input', () => {
    document.getElementById('min-cash-label').textContent = minCash.value + '%';
  });
}

// ── 表单提交 ─────────────────────────────────────────────────────────────────
function setupForm() {
  document.getElementById('backtest-form').addEventListener('submit', async e => {
    e.preventDefault();
    clearError();

    if (selectedSymbols.size === 0) {
      showError('请至少添加一只股票代码');
      return;
    }

    const strategyId = document.getElementById('strategy-select').value;
    const startDate = document.getElementById('start-date').value;
    const endDate = document.getElementById('end-date').value;
    const capital = parseFloat(document.getElementById('initial-capital').value);

    const payload = {
      strategy_id: strategyId,
      symbols: [...selectedSymbols],
      start_date: startDate,
      end_date: endDate,
      initial_capital: capital,
      strategy_params: getStrategyParams(),
      risk: {
        max_position_pct: parseInt(document.getElementById('max-pos-pct').value) / 100,
        min_cash_reserve: parseInt(document.getElementById('min-cash-reserve').value) / 100,
      },
    };

    try {
      setRunning(true);
      const resp = await API.runBacktest(payload);
      currentRunId = resp.run_id;
      showStatusCard(strategyId);
      startSSE(currentRunId);
    } catch (e) {
      showError(e.message);
      setRunning(false);
    }
  });
}

// ── SSE 进度 ─────────────────────────────────────────────────────────────────
function startSSE(runId) {
  if (sseSource) sseSource.close();
  sseSource = new EventSource(`/api/backtest/${runId}/stream`);

  sseSource.addEventListener('progress', e => {
    const data = JSON.parse(e.data);
    updateProgress(data);
  });

  sseSource.addEventListener('completed', e => {
    const data = JSON.parse(e.data);
    sseSource.close();
    setRunning(false);
    loadHistory();
    window.location.href = data.redirect;
  });

  sseSource.addEventListener('error', e => {
    const data = JSON.parse(e.data);
    sseSource.close();
    setRunning(false);
    showError(data.message || '回测失败');
    loadHistory();
  });

  sseSource.onerror = () => {
    // SSE 连接断开，回退到轮询
    sseSource.close();
    pollStatus(runId);
  };
}

async function pollStatus(runId) {
  while (true) {
    await sleep(1000);
    try {
      const status = await API.getStatus(runId);
      if (status.status === 'running') {
        updateProgress(status);
      } else if (status.status === 'completed') {
        setRunning(false);
        loadHistory();
        window.location.href = `/result.html?run_id=${runId}`;
        return;
      } else if (status.status === 'failed') {
        setRunning(false);
        showError(status.error || '回测失败');
        loadHistory();
        return;
      }
    } catch (_) {}
  }
}

function updateProgress(data) {
  document.getElementById('progress-bar').style.width = data.progress + '%';
  document.getElementById('progress-text').textContent = data.progress + '%';
  document.getElementById('current-date-text').textContent = data.current_date || '';
  document.getElementById('status-elapsed').textContent =
    (data.elapsed_seconds || 0).toFixed(1) + 's';
  if (data.total_assets) {
    document.getElementById('status-assets').textContent =
      Fmt.money(data.total_assets);
  }
}

// ── 历史记录 ─────────────────────────────────────────────────────────────────
async function loadHistory() {
  const data = await API.getHistory().catch(() => ({ runs: [] }));
  const container = document.getElementById('history-list');

  if (!data.runs.length) {
    container.innerHTML = '<p class="text-sm text-gray-600">暂无记录</p>';
    return;
  }

  container.innerHTML = data.runs.map(run => {
    const ret = run.total_return;
    const retStr = ret != null ? Fmt.pct(ret) : '运行中';
    const retColor = ret != null ? Fmt.colorClass(ret) : 'text-gray-400';
    return `
      <div class="history-card" onclick="goToResult('${run.run_id}', '${run.status}')">
        <div>
          <p class="text-sm font-medium text-gray-200">${run.strategy_name}</p>
          <p class="text-xs text-gray-500 mt-0.5">${run.symbols.join(', ')} · ${run.start_date} → ${run.end_date}</p>
        </div>
        <div class="text-right">
          <p class="text-sm font-semibold ${retColor}">${retStr}</p>
          <p class="text-xs text-gray-600">${run.status === 'completed' ? '夏普 ' + (run.sharpe_ratio?.toFixed(2) ?? '—') : run.status}</p>
        </div>
      </div>
    `;
  }).join('');
}

function goToResult(runId, status) {
  if (status === 'completed') {
    window.location.href = `/result.html?run_id=${runId}`;
  }
}

// ── UI 辅助 ──────────────────────────────────────────────────────────────────
function showStatusCard(strategyId) {
  const card = document.getElementById('run-status-card');
  card.classList.remove('hidden');
  document.getElementById('status-strategy').textContent = strategyId;
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('progress-text').textContent = '0%';
}

function setRunning(running) {
  const btn = document.getElementById('run-btn');
  btn.disabled = running;
  btn.textContent = running ? '回测中...' : '运行回测';
}

function showError(msg) {
  const el = document.getElementById('form-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

function clearError() {
  document.getElementById('form-error').classList.add('hidden');
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
