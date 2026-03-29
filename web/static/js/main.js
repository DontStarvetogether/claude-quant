/**
 * 主页交互逻辑
 */

// ── 股票池（从后端动态加载，替代硬编码列表）────────────────────────────────
let QUICK_SYMBOLS = [];

// ── 状态 ─────────────────────────────────────────────────────────────────────
let strategies = [];
let selectedSymbols = new Set();
let currentRunId = null;
let sseSource = null;

// 显示配置
const DISPLAY_CONFIG = {
  maxVisible: 20,           // 默认最多显示的股票数
  isExpanded: false,         // 是否展开全部
  searchTerm: '',            // 搜索词
  quickMaxVisible: 30,       // 常用股票默认显示数量
  quickIsExpanded: false,    // 常用股票是否展开全部
  quickSearchTerm: ''         // 常用股票搜索词
};

// ── 初始化 ───────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  initDateDefaults();
  await loadStrategies();
  await loadQuickSymbols();
  setupSymbolInput();
  setupRiskSliders();
  buildQuickSymbols();
  setupSelectAll();
  setupForm();
  loadHistory();
});

// ── 加载股票池 ───────────────────────────────────────────────────────────────
async function loadQuickSymbols() {
  try {
    const data = await API.getSymbols(true);
    QUICK_SYMBOLS = data.symbols || [];
  } catch (e) {
    console.error('加载股票池失败', e);
    // 加载失败时使用默认股票
    QUICK_SYMBOLS = [
      { symbol: '600519.SH', name: '贵州茅台' },
      { symbol: '000858.SZ', name: '五粮液' },
      { symbol: '601318.SH', name: '中国平安' },
      { symbol: '600036.SH', name: '招商银行' },
      { symbol: '000333.SZ', name: '美的集团' },
      { symbol: '002594.SZ', name: '比亚迪' },
      { symbol: '601888.SH', name: '中国中免' },
      { symbol: '600276.SH', name: '恒瑞医药' },
      { symbol: '601166.SH', name: '兴业银行' },
      { symbol: '600887.SH', name: '伊利股份' },
    ];
  }
}

// ── 日期默认值（近 3 年）────────────────────────────────────────────────────
function initDateDefaults() {
  const today = new Date();
  const threeYearsAgo = new Date(today);
  threeYearsAgo.setFullYear(today.getFullYear() - 3);

  document.getElementById('end-date').value = today.toISOString().slice(0, 10);
  document.getElementById('start-date').value = threeYearsAgo.toISOString().slice(0, 10);
}

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
  updateSymbolDisplay();
  buildQuickSymbols();
  document.getElementById('symbol-input').value = '';
}

function removeSymbol(symbol) {
  selectedSymbols.delete(symbol);
  updateSymbolDisplay();
  buildQuickSymbols();
}

function updateSymbolDisplay() {
  renderSymbolTags();
}

function renderSymbolTags() {
  const container = document.getElementById('symbol-tags');
  const section = document.getElementById('selected-symbols-section');
  const countSpan = document.getElementById('selected-count');
  
  // 更新统计信息
  countSpan.textContent = selectedSymbols.size;
  
  // 控制区域显示
  if (selectedSymbols.size === 0) {
    section.classList.add('hidden');
    container.innerHTML = '';
    return;
  }
  
  section.classList.remove('hidden');
  
  // 获取所有选中的股票
  const allSymbols = [...selectedSymbols];
  const nameMap = Object.fromEntries(QUICK_SYMBOLS.map(s => [s.symbol, s.name]));
  
  // 根据搜索词过滤
  let filteredSymbols = allSymbols;
  if (DISPLAY_CONFIG.searchTerm) {
    const term = DISPLAY_CONFIG.searchTerm.toLowerCase();
    filteredSymbols = allSymbols.filter(sym => {
      const name = nameMap[sym] || '';
      return sym.toLowerCase().includes(term) || name.toLowerCase().includes(term);
    });
  }
  
  // 根据展开状态决定显示数量
  const visibleSymbols = DISPLAY_CONFIG.isExpanded 
    ? filteredSymbols 
    : filteredSymbols.slice(0, DISPLAY_CONFIG.maxVisible);
  
  // 渲染股票标签
  if (visibleSymbols.length === 0) {
    container.innerHTML = '<span class="text-xs text-gray-500">没有匹配的股票</span>';
    return;
  }
  
  container.innerHTML = visibleSymbols.map(sym => {
    const name = nameMap[sym] || '';
    return `
      <span class="inline-flex items-center gap-1 px-3 py-1 bg-blue-900/40 border border-blue-700/50 rounded-full text-xs text-blue-300">
        ${sym}${name ? ' · ' + name : ''}
        <button onclick="removeSymbol('${sym}')" class="text-blue-500 hover:text-red-400 ml-1">×</button>
      </span>
    `;
  }).join('');
  
  // 显示"还有更多"提示
  if (!DISPLAY_CONFIG.isExpanded && filteredSymbols.length > DISPLAY_CONFIG.maxVisible) {
    const remaining = filteredSymbols.length - DISPLAY_CONFIG.maxVisible;
    container.innerHTML += `
      <span class="text-xs text-gray-500 px-2 py-1">
        还有 ${remaining} 只股票...
      </span>
    `;
  }
}

// ── 快速选择 ─────────────────────────────────────────────────────────────────
function buildQuickSymbols() {
  const container = document.getElementById('quick-symbol-container');
  
  // 根据搜索词过滤
  let filteredSymbols = QUICK_SYMBOLS;
  if (DISPLAY_CONFIG.quickSearchTerm) {
    const term = DISPLAY_CONFIG.quickSearchTerm.toLowerCase();
    filteredSymbols = QUICK_SYMBOLS.filter(s => {
      const displayText = s.name || s.symbol;
      return s.symbol.toLowerCase().includes(term) || displayText.toLowerCase().includes(term);
    });
  }
  
  // 根据展开状态决定显示数量
  const visibleSymbols = DISPLAY_CONFIG.quickIsExpanded 
    ? filteredSymbols 
    : filteredSymbols.slice(0, DISPLAY_CONFIG.quickMaxVisible);
  
  // 渲染股票按钮
  if (visibleSymbols.length === 0) {
    container.innerHTML = '<span class="text-xs text-gray-500">没有匹配的股票</span>';
    return;
  }
  
  container.innerHTML = visibleSymbols.map(s => {
    const displayText = s.name || s.symbol;
    const isSelected = selectedSymbols.has(s.symbol);
    const btnClass = isSelected 
      ? 'px-3 py-1 bg-blue-900/60 border border-blue-600/60 rounded-full text-xs text-blue-200'
      : 'px-3 py-1 bg-gray-800 border border-gray-700 rounded-full text-xs hover:border-blue-500 transition-colors';
    
    return `
    <button type="button"
      data-symbol="${s.symbol}"
      class="quick-symbol ${btnClass}">
      ${displayText}
    </button>
    `;
  }).join('');
  
  // 显示"还有更多"提示
  if (!DISPLAY_CONFIG.quickIsExpanded && filteredSymbols.length > DISPLAY_CONFIG.quickMaxVisible) {
    const remaining = filteredSymbols.length - DISPLAY_CONFIG.quickMaxVisible;
    container.innerHTML += `
      <span class="text-xs text-gray-500 px-2 py-1">
        还有 ${remaining} 只股票...
      </span>
    `;
  }

  container.querySelectorAll('.quick-symbol').forEach(btn => {
    btn.addEventListener('click', () => {
      const sym = btn.dataset.symbol;
      if (selectedSymbols.has(sym)) {
        selectedSymbols.delete(sym);
      } else {
        selectedSymbols.add(sym);
      }
      updateSymbolDisplay();
      buildQuickSymbols(); // 重新渲染常用股票
    });
  });
}

function setupSelectAll() {
  document.getElementById('select-all-btn').addEventListener('click', () => {
    QUICK_SYMBOLS.forEach(s => selectedSymbols.add(s.symbol));
    updateSymbolDisplay();
    buildQuickSymbols();
  });

  document.getElementById('clear-all-btn').addEventListener('click', () => {
    selectedSymbols.clear();
    updateSymbolDisplay();
    buildQuickSymbols();
  });
  
  // 已选股票搜索功能
  document.getElementById('symbol-search').addEventListener('input', (e) => {
    DISPLAY_CONFIG.searchTerm = e.target.value;
    updateSymbolDisplay();
  });
  
  // 已选股票展开/折叠功能
  document.getElementById('toggle-expand-btn').addEventListener('click', () => {
    DISPLAY_CONFIG.isExpanded = !DISPLAY_CONFIG.isExpanded;
    const btn = document.getElementById('toggle-expand-btn');
    btn.textContent = DISPLAY_CONFIG.isExpanded ? '收起' : '展开全部';
    updateSymbolDisplay();
  });
  
  // 常用股票搜索功能
  document.getElementById('quick-symbol-search').addEventListener('input', (e) => {
    DISPLAY_CONFIG.quickSearchTerm = e.target.value;
    buildQuickSymbols();
  });
  
  // 常用股票展开/折叠功能
  document.getElementById('quick-toggle-expand-btn').addEventListener('click', () => {
    DISPLAY_CONFIG.quickIsExpanded = !DISPLAY_CONFIG.quickIsExpanded;
    const btn = document.getElementById('quick-toggle-expand-btn');
    btn.textContent = DISPLAY_CONFIG.quickIsExpanded ? '收起' : '展开全部';
    buildQuickSymbols();
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
  const pct = data.progress || 0;
  document.getElementById('progress-bar').style.width = pct + '%';
  document.getElementById('progress-text').textContent = pct + '%';
  document.getElementById('current-date-text').textContent = data.current_date || '';
  document.getElementById('status-elapsed').textContent =
    (data.elapsed_seconds || 0).toFixed(1) + 's';

  // 根据阶段更新标题
  const dateText = data.current_date || '';
  const isDownloading = dateText.startsWith('下载') || dateText.startsWith('同步');
  document.getElementById('status-title').textContent =
    isDownloading ? '数据准备中' : '回测运行中';

  if (data.total_assets) {
    document.getElementById('status-assets').textContent = Fmt.money(data.total_assets);
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
  document.getElementById('status-title').textContent = '数据准备中';
  document.getElementById('status-strategy').textContent = strategyId;
  document.getElementById('progress-bar').style.width = '0%';
  document.getElementById('progress-text').textContent = '0%';
  document.getElementById('current-date-text').textContent = '';
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
