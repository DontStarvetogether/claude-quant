/**
 * 模拟盘页面交互逻辑
 */

let strategies = [];
let allSymbols = [];
let selectedSymbols = new Set();
let currentSessionId = null;
let equityChart = null;
let currentMode = 'paper'; // "paper" | "live"

// 多会话并行：每个 session 独立的状态
let sessionStates = {};  // { [sid]: { equityData:[], trades:[], metrics:null, sse:EventSource|null, ... } }

function _session(sid) {
  if (!sid) sid = currentSessionId;
  if (!sid || !sessionStates[sid]) return null;
  return sessionStates[sid];
}
function _cur() { return _session(currentSessionId); }
function _ensure(sid) {
  if (!sessionStates[sid]) sessionStates[sid] = { equityData: [], trades: [], metrics: null, sse: null, lastStatus: null };
  return sessionStates[sid];
}

const Fmt = {
  money: v => v == null ? '--' : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  pct: v => v == null ? '--' : (v * 100).toFixed(2) + '%',
  price: v => v == null ? '--' : v.toFixed(2),
  elapsed: v => {
    if (!v || v <= 0) return '--';
    const total = Math.round(v);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h > 0) return h + '时' + m + '分' + s + '秒';
    if (m > 0) return m + '分' + s + '秒';
    return s + '秒';
  },
  num: (v, d) => v == null ? '--' : v.toFixed(d),
  datetime: v => {
    if (!v) return '--';
    try { return new Date(v).toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' }); }
    catch { return v; }
  },
};

// ── 初始化 ───────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  initDateDefaults();
  setupModeToggle();
  await Promise.all([loadStrategies(), loadAllSymbols()]);
  setupSymbolInput();
  setupForm();
  await loadSessions();
});

function setupModeToggle() {
  const toggle = document.getElementById('mode-toggle');
  if (!toggle) return;

  toggle.addEventListener('click', e => {
    const btn = e.target.closest('[data-mode]');
    if (!btn) return;
    setMode(btn.dataset.mode);
  });
}

function setMode(mode) {
  currentMode = mode;
  const btns = document.querySelectorAll('#mode-toggle [data-mode]');
  btns.forEach(b => {
    if (b.dataset.mode === mode) {
      b.className = 'px-4 py-1.5 text-sm font-medium transition-colors ' +
        (mode === 'live' ? 'bg-red-600 text-white' : 'bg-blue-600 text-white');
    } else {
      b.className = 'px-4 py-1.5 text-sm font-medium transition-colors text-gray-400 hover:text-gray-200';
    }
  });

  const hint = document.getElementById('mode-hint');
  const dateSection = document.getElementById('start-date').closest('.grid.grid-cols-2').parentElement;
  const qmtConfig = document.getElementById('qmt-config');
  const startBtn = document.getElementById('start-btn');

  if (mode === 'live') {
    hint.textContent = '连接 QMT 券商实盘交易，请确保 QMT 客户端已启动';
    hint.className = 'text-xs text-red-400';
    dateSection.classList.add('hidden');
    qmtConfig.classList.remove('hidden');
    startBtn.textContent = '启动实盘';
    startBtn.className = startBtn.className.replace('bg-green-600 hover:bg-green-500', 'bg-red-600 hover:bg-red-500');
  } else {
    hint.textContent = '使用历史数据回放，不连接券商';
    hint.className = 'text-xs text-gray-500';
    dateSection.classList.remove('hidden');
    qmtConfig.classList.add('hidden');
    startBtn.textContent = '启动模拟盘';
    startBtn.className = startBtn.className.replace('bg-red-600 hover:bg-red-500', 'bg-green-600 hover:bg-green-500');
  }
}

function initDateDefaults() {
  const today = new Date();
  const end = new Date(today);
  end.setDate(end.getDate() - 1);
  const start = new Date(end);
  start.setDate(start.getDate() - 90);

  document.getElementById('start-date').value = fmtDate(start);
  document.getElementById('end-date').value = fmtDate(end);

  document.getElementById('date-shortcuts')?.addEventListener('click', e => {
    const btn = e.target.closest('[data-months]');
    if (!btn) return;
    const months = parseInt(btn.dataset.months);
    const ed = new Date();
    ed.setDate(ed.getDate() - 1);
    const st = new Date(ed);
    st.setMonth(st.getMonth() - months);
    document.getElementById('start-date').value = fmtDate(st);
    document.getElementById('end-date').value = fmtDate(ed);
  });
}

function fmtDate(d) {
  return d.toISOString().slice(0, 10);
}

// ── 策略加载 ─────────────────────────────────────────────────────────────────

async function loadStrategies() {
  try {
    const res = await fetch('/api/strategies');
    const data = await res.json();
    strategies = data.strategies || [];
    renderStrategySelect();
  } catch (e) {
    console.error('加载策略失败', e);
  }
}

function renderStrategySelect() {
  const sel = document.getElementById('strategy-select');
  sel.innerHTML = strategies.map(s =>
    `<option value="${s.id}">${s.name} — ${s.description}</option>`
  ).join('');

  sel.addEventListener('change', renderStrategyParams);
  renderStrategyParams();
}

function renderStrategyParams() {
  const sid = document.getElementById('strategy-select').value;
  const s = strategies.find(x => x.id === sid);
  const section = document.getElementById('strategy-params');
  const container = document.getElementById('params-container');

  if (!s || !s.params || s.params.length === 0) {
    section.classList.add('hidden');
    return;
  }

  section.classList.remove('hidden');
  container.innerHTML = s.params.map(p => `
    <div>
      <label class="block text-xs text-gray-500 mb-1">${p.label}</label>
      <input type="number" data-param="${p.name}"
        value="${p.default}" min="${p.min ?? ''}" max="${p.max ?? ''}" step="${p.step ?? 'any'}"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500" />
    </div>
  `).join('');
}

function getStrategyParams() {
  const params = {};
  document.querySelectorAll('#params-container input[data-param]').forEach(el => {
    const v = parseFloat(el.value);
    if (!isNaN(v)) params[el.dataset.param] = v;
  });
  return params;
}

// ── 股票搜索 ─────────────────────────────────────────────────────────────────

let quickExpanded = false;
const QUICK_DISPLAY = 25;

async function loadAllSymbols() {
  try {
    const res = await fetch('/api/symbols?sync=false');
    const data = await res.json();
    allSymbols = data.symbols || [];
  } catch (e) {
    console.error('加载股票池失败', e);
    allSymbols = [
      { symbol: '600519.SH', name: '贵州茅台' },
      { symbol: '000858.SZ', name: '五粮液' },
      { symbol: '601318.SH', name: '中国平安' },
    ];
  }
  renderQuickSymbols();
  setupQuickButtons();
}

function renderQuickSymbols() {
  const container = document.getElementById('quick-symbol-container');
  if (!container || !allSymbols.length) return;
  const count = quickExpanded ? allSymbols.length : QUICK_DISPLAY;
  const remaining = allSymbols.length - QUICK_DISPLAY;
  container.innerHTML = allSymbols.slice(0, count).map(s =>
    `<button type="button" onclick="addSymbol('${s.symbol}')"
      class="px-2.5 py-1 text-xs bg-gray-800 border border-gray-700 rounded hover:border-blue-500 hover:text-blue-400 text-gray-400 transition-colors">${s.name || s.symbol}</button>`
  ).join('');
  const expandBtn = document.getElementById('quick-toggle-expand-btn');
  if (expandBtn) expandBtn.textContent = (!quickExpanded && remaining > 0) ? `展开全部（+${remaining}）` : '收起';
}

function setupQuickButtons() {
  document.getElementById('quick-toggle-expand-btn')?.addEventListener('click', () => {
    quickExpanded = !quickExpanded;
    renderQuickSymbols();
  });
  document.getElementById('select-all-btn')?.addEventListener('click', () => {
    allSymbols.slice(0, 50).forEach(s => selectedSymbols.add(s.symbol));
    renderSymbolTags();
  });
  document.getElementById('clear-all-btn')?.addEventListener('click', () => {
    selectedSymbols.clear();
    renderSymbolTags();
  });
}

function setupSymbolInput() {
  const input = document.getElementById('symbol-input');
  const btn = document.getElementById('add-symbol-btn');
  const dropdown = document.getElementById('symbol-dropdown');
  let activeIdx = -1;

  function getMatches(q) {
    if (!q) return [];
    const term = q.toLowerCase();
    return allSymbols.filter(s =>
      s.symbol.toLowerCase().includes(term) ||
      (s.name && s.name.toLowerCase().includes(term))
    ).slice(0, 12);
  }

  function renderDropdown(matches) {
    if (!matches.length) { dropdown.classList.add('hidden'); return; }
    activeIdx = -1;
    dropdown.innerHTML = matches.map((s, i) => `
      <div data-idx="${i}" data-symbol="${s.symbol}"
        class="flex items-center justify-between px-3 py-2 cursor-pointer hover:bg-gray-700 text-sm">
        <span class="font-mono text-gray-300">${s.symbol}</span>
        <span class="text-gray-400 text-xs ml-3">${s.name || ''}</span>
      </div>`).join('');
    dropdown.classList.remove('hidden');

    dropdown.querySelectorAll('[data-symbol]').forEach(row => {
      row.addEventListener('mousedown', e => {
        e.preventDefault();
        pickSymbol(row.dataset.symbol);
      });
    });
  }

  function pickSymbol(symbol) {
    addSymbol(symbol);
    input.value = '';
    dropdown.classList.add('hidden');
    activeIdx = -1;
  }

  function highlightItem(idx) {
    const items = dropdown.querySelectorAll('[data-idx]');
    items.forEach(el => el.classList.remove('bg-gray-700'));
    if (idx >= 0 && items[idx]) items[idx].classList.add('bg-gray-700');
  }

  input.addEventListener('input', () => {
    renderDropdown(getMatches(input.value.trim()));
  });

  input.addEventListener('keydown', e => {
    const items = dropdown.querySelectorAll('[data-idx]');
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      activeIdx = Math.min(activeIdx + 1, items.length - 1);
      highlightItem(activeIdx);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      activeIdx = Math.max(activeIdx - 1, -1);
      highlightItem(activeIdx);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (activeIdx >= 0 && items[activeIdx]) {
        pickSymbol(items[activeIdx].dataset.symbol);
      } else {
        const val = input.value.trim();
        if (val) { addSymbol(normalizeSymbol(val)); input.value = ''; }
        dropdown.classList.add('hidden');
      }
    } else if (e.key === 'Escape') {
      dropdown.classList.add('hidden');
      activeIdx = -1;
    }
  });

  input.addEventListener('blur', () => {
    setTimeout(() => dropdown.classList.add('hidden'), 150);
  });

  btn.addEventListener('click', () => {
    const val = input.value.trim();
    if (!val) return;
    const matches = getMatches(val);
    if (matches.length === 1) {
      pickSymbol(matches[0].symbol);
    } else {
      addSymbol(normalizeSymbol(val));
      input.value = '';
      dropdown.classList.add('hidden');
    }
  });
}

function normalizeSymbol(s) {
  s = s.trim().toUpperCase();
  if (/^\d{6}$/.test(s)) {
    return s.startsWith('6') ? s + '.SH' : s + '.SZ';
  }
  return s;
}

function addSymbol(symbol) {
  if (!symbol) return;
  symbol = symbol.toUpperCase();
  selectedSymbols.add(symbol);
  renderSymbolTags();
}

function removeSymbol(symbol) {
  selectedSymbols.delete(symbol);
  renderSymbolTags();
}

function renderSymbolTags() {
  const container = document.getElementById('symbol-tags');
  if (selectedSymbols.size === 0) {
    container.innerHTML = '<span class="text-xs text-gray-600">请搜索并添加股票</span>';
    return;
  }
  container.innerHTML = [...selectedSymbols].map(sym => {
    const info = allSymbols.find(s => s.symbol === sym);
    const name = info ? info.name : '';
    return `<span class="inline-flex items-center gap-1 px-2 py-1 bg-gray-800 border border-gray-700 rounded text-xs text-gray-300">
      <span class="font-mono">${sym}</span>
      ${name ? `<span class="text-gray-500">${name}</span>` : ''}
      <button type="button" onclick="removeSymbol('${sym}')" class="ml-1 text-gray-500 hover:text-red-400">&times;</button>
    </span>`;
  }).join('');
}

// ── 表单提交 ─────────────────────────────────────────────────────────────────

function setupForm() {
  document.getElementById('live-form').addEventListener('submit', async e => {
    e.preventDefault();
    await startSession();
  });

  document.getElementById('stop-btn').addEventListener('click', async () => {
    if (!currentSessionId) return;
    try {
      await fetch(`/api/live/${currentSessionId}/stop`, { method: 'POST' });
      // 停止该会话的 SSE
      const st = _session(currentSessionId);
      if (st && st.sse) { st.sse.close(); st.sse = null; }
      document.getElementById('stop-btn').disabled = true;
    } catch (e) {
      console.error('停止失败', e);
    }
  });
}

async function startSession() {
  const errorEl = document.getElementById('form-error');
  errorEl.classList.add('hidden');

  const strategyId = document.getElementById('strategy-select').value;
  const capital = parseFloat(document.getElementById('initial-capital').value) || 1000000;

  if (!strategyId) return showError('请选择策略');
  if (selectedSymbols.size === 0) return showError('请添加至少一只股票');

  const symbols = [...selectedSymbols];

  const payload = {
    strategy_id: strategyId,
    symbols,
    mode: currentMode,
    initial_capital: capital,
    strategy_params: getStrategyParams(),
    risk: { max_position_pct: 0.95, min_cash_reserve: 0.05 },
  };

  if (currentMode === 'paper') {
    const startDate = document.getElementById('start-date').value;
    const endDate = document.getElementById('end-date').value;
    if (!startDate || !endDate) return showError('请选择日期范围');
    payload.start_date = startDate;
    payload.end_date = endDate;
  } else {
    // 实盘模式
    const accountId = document.getElementById('qmt-account-id').value.trim();
    const qmtDir = document.getElementById('qmt-dir').value.trim();
    if (!accountId) return showError('实盘模式需要填写 QMT 资金账号');
    payload.account_id = accountId;
    if (qmtDir) payload.mini_qmt_dir = qmtDir;

    // 二次确认
    const confirmed = confirm(
      '即将启动实盘交易！\n\n' +
      `策略：${strategyId}\n` +
      `账号：${accountId}\n` +
      `标的：${symbols.join(', ')}\n` +
      `初始资金：${capital.toLocaleString()} 元\n\n` +
      '请确认 QMT 客户端已启动且登录。\n' +
      '实盘交易将产生真实的买卖委托，是否继续？'
    );
    if (!confirmed) return;
  }

  const startBtn = document.getElementById('start-btn');
  startBtn.disabled = true;

  try {
    const res = await fetch('/api/live/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '启动失败');
    }

    const data = await res.json();
    _ensure(data.session_id);
    currentSessionId = data.session_id;
    document.getElementById('stop-btn').disabled = false;

    showStatusSection();
    startSSE(currentSessionId);

  } catch (e) {
    showError(e.message);
    document.getElementById('start-btn').disabled = false;
  }
}

function showError(msg) {
  const el = document.getElementById('form-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ── SSE 实时推送 ─────────────────────────────────────────────────────────────

function startSSE(sessionId) {
  const st = _ensure(sessionId);
  if (st.sse) st.sse.close();
  st.sse = new EventSource(`/api/live/${sessionId}/stream`);

  st.sse.addEventListener('status', e => {
    const data = JSON.parse(e.data);
    _ensure(sessionId).lastStatus = data;
    updateDashboard(data);
    refreshSessionsList();
  });

  st.sse.addEventListener('stopped', e => {
    const data = JSON.parse(e.data);
    _ensure(sessionId).lastStatus = data;
    updateDashboard(data);
    onSessionEnd(sessionId, 'stopped');
  });

  st.sse.addEventListener('error', e => {
    if (e.data) {
      const data = JSON.parse(e.data);
      showError(data.error || '会话异常');
    }
    onSessionEnd(sessionId, 'failed');
  });

  st.sse.onerror = () => {
    st.sse.close();
    pollStatus(sessionId);
  };
}

async function pollStatus(sessionId) {
  while (true) {
    await sleep(1000);
    try {
      const res = await fetch(`/api/live/${sessionId}/status`);
      const data = await res.json();
      _ensure(sessionId).lastStatus = data;
      if (currentSessionId === sessionId) updateDashboard(data);
      if (data.status === 'stopped' || data.status === 'failed') {
        onSessionEnd(sessionId, data.status);
        return;
      }
    } catch (e) {
      return;
    }
  }
}

function sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

function onSessionEnd(sessionId, status) {
  const st = _session(sessionId);
  if (st && st.sse) { st.sse.close(); st.sse = null; }

  if (currentSessionId === sessionId) {
    document.getElementById('stop-btn').disabled = true;
    const label = document.getElementById('status-label');
    label.textContent = status === 'stopped' ? '已停止' : '异常';
    label.className = 'text-sm font-semibold ' + (status === 'stopped' ? 'text-gray-400' : 'text-red-400');
  }

  loadSessions();
}

// ── 仪表盘更新 ───────────────────────────────────────────────────────────────

function showStatusSection() {
  document.getElementById('status-section').classList.remove('hidden');
  document.getElementById('positions-section').classList.remove('hidden');
  document.getElementById('trades-section').classList.remove('hidden');
  document.getElementById('config-section').removeAttribute('open');
}

function showMetricsSection() {
  document.getElementById('metrics-section').classList.remove('hidden');
  document.getElementById('equity-section').classList.remove('hidden');
  document.getElementById('monthly-section').classList.remove('hidden');
}

function updateDashboard(data) {
  const sid = data.session_id || currentSessionId;
  if (!sid) return;
  if (currentSessionId !== sid) return; // 只渲染当前查看的会话

  showStatusSection();

  const st = _ensure(sid);
  const eq = st.equityData;

  // 追加净值数据点（按日期去重）
  if (data.current_date && data.total_assets != null) {
    const last = eq[eq.length - 1];
    if (!last || last.date !== data.current_date) {
      eq.push({ date: data.current_date, total_assets: data.total_assets, cash: data.cash || 0 });
    } else {
      last.total_assets = data.total_assets;
      last.cash = data.cash || 0;
    }
  }

  // 缓存成交数据（按日期排序，确保早→晚）
  if (data.recent_trades) {
    st.trades = [...data.recent_trades].sort((a, b) => (a.trade_date || '').localeCompare(b.trade_date || ''));
  }
  if (data.metrics) st.metrics = data.metrics;

  // 状态卡片
  const statusMap = { starting: '启动中', running: '运行中', stopped: '已停止', failed: '异常' };
  const colorMap = { starting: 'text-yellow-400', running: 'text-green-400', stopped: 'text-gray-400', failed: 'text-red-400' };

  const label = document.getElementById('status-label');
  const modeTag = data.mode === 'live' ? '<span class="text-red-400">实盘</span>' : '<span class="text-blue-400">模拟</span>';
  label.innerHTML = `${modeTag} · <span class="${colorMap[data.status] || 'text-gray-400'}">${statusMap[data.status] || data.status}</span>`;

  document.getElementById('status-date').textContent = data.current_date || '--';
  document.getElementById('status-assets').textContent = Fmt.money(data.total_assets);
  document.getElementById('status-cash').textContent = Fmt.money(data.cash);
  document.getElementById('status-elapsed').textContent = (data.elapsed_seconds || 0).toFixed(1) + 's';

  // 绩效指标卡片 + 月度收益
  if (data.metrics) {
    renderMetricsCards(data.metrics);
    if (eq.length > 0) renderMonthlyReturns(eq);
  }

  // 净值图表（传递成交数据用于买卖标记）
  initEquityChart();
  updateEquityChart(data.recent_trades, eq);

  // 持仓表格
  const posBody = document.getElementById('positions-body');
  if (data.positions && data.positions.length > 0) {
    posBody.innerHTML = data.positions.map(p => {
      const pnlColor = p.unrealized_pnl >= 0 ? 'text-red-400' : 'text-green-400';
      return `<tr class="hover:bg-gray-800/50">
        <td class="px-3 py-2 text-left text-gray-200">${p.symbol}</td>
        <td class="px-3 py-2 text-right">${p.total_qty}</td>
        <td class="px-3 py-2 text-right">${Fmt.price(p.avg_cost)}</td>
        <td class="px-3 py-2 text-right">${Fmt.price(p.last_price)}</td>
        <td class="px-3 py-2 text-right">${Fmt.money(p.market_value)}</td>
        <td class="px-3 py-2 text-right ${pnlColor}">${Fmt.money(p.unrealized_pnl)}</td>
        <td class="px-3 py-2 text-right ${pnlColor}">${Fmt.pct(p.unrealized_pnl_pct)}</td>
      </tr>`;
    }).join('');
  } else {
    posBody.innerHTML = '<tr><td colspan="7" class="px-3 py-4 text-center text-gray-600">暂无持仓</td></tr>';
  }

  // 成交流水
  const tradeBody = document.getElementById('trades-body');
  const tradeCount = document.getElementById('trades-count');
  const exportBtn = document.getElementById('export-trades-btn');
  const allTrades = data.recent_trades || [];
  if (allTrades.length > 0) {
    tradeCount.textContent = `共 ${allTrades.length} 笔`;
    exportBtn.classList.remove('hidden');
    exportBtn.onclick = () => exportTradesCSV(allTrades);
    const nameMap = {};
    (allSymbols || []).forEach(s => { nameMap[s.symbol] = s.name || ''; });
    // 按日期排序（早→晚）
    const sortedTrades = [...allTrades].sort((a, b) => (a.trade_date || '').localeCompare(b.trade_date || ''));
    tradeBody.innerHTML = sortedTrades.map(t => {
      const sideColor = t.side === 'BUY' ? 'text-red-400' : 'text-green-400';
      const sideText = t.side === 'BUY' ? '买入' : '卖出';
      const net = t.side === 'BUY'
        ? -(t.amount + (t.commission || 0))
        : (t.amount - (t.commission || 0) - (t.stamp_tax || 0));
      const sName = nameMap[t.symbol] || '';
      return `<tr class="hover:bg-gray-800/50">
        <td class="px-3 py-2 text-left text-gray-400">${t.trade_date}</td>
        <td class="px-3 py-2 text-left text-gray-200">${t.symbol}${sName ? ' <span class=\"text-gray-500 text-xs\">' + sName + '</span>' : ''}</td>
        <td class="px-3 py-2 text-center ${sideColor}">${sideText}</td>
        <td class="px-3 py-2 text-right">${Fmt.price(t.price)}</td>
        <td class="px-3 py-2 text-right">${t.quantity}</td>
        <td class="px-3 py-2 text-right">${Fmt.money(t.amount)}</td>
        <td class="px-3 py-2 text-right text-gray-500">${Fmt.money(t.commission)}</td>
        <td class="px-3 py-2 text-right text-gray-500">${Fmt.money(t.stamp_tax)}</td>
        <td class="px-3 py-2 text-right ${net >= 0 ? 'text-red-400' : 'text-green-400'}">${Fmt.money(net)}</td>
      </tr>`;
    }).join('');
  } else {
    tradeCount.textContent = '';
    exportBtn.classList.add('hidden');
    tradeBody.innerHTML = '<tr><td colspan="9" class="px-3 py-4 text-center text-gray-600">暂无成交</td></tr>';
  }
}

function renderMetricsCards(m) {
  showMetricsSection();
  const cards = document.getElementById('metrics-cards');

  const items = [
    { label: '总收益率', value: Fmt.pct(m.total_return), color: m.total_return >= 0 ? 'text-red-400' : 'text-green-400' },
    { label: '年化收益率', value: Fmt.pct(m.annual_return), color: m.annual_return >= 0 ? 'text-red-400' : 'text-green-400' },
    { label: '最大回撤', value: Fmt.pct(m.max_drawdown), color: 'text-green-400' },
    { label: '夏普比率', value: (m.sharpe_ratio || 0).toFixed(3), color: m.sharpe_ratio >= 1 ? 'text-green-400' : 'text-gray-300' },
    { label: '胜率', value: Fmt.pct(m.win_rate), color: 'text-gray-200' },
    { label: '总交易次数', value: m.total_trades || 0, color: 'text-gray-200' },
    { label: '总手续费', value: Fmt.money(m.total_fees), color: 'text-gray-400' },
    { label: '盈亏比', value: (m.profit_factor || 0).toFixed(2), color: m.profit_factor >= 1.5 ? 'text-green-400' : 'text-gray-200' },
  ];

  cards.innerHTML = items.map(i => `
    <div class="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3">
      <p class="text-[10px] text-gray-500 uppercase tracking-wide">${i.label}</p>
      <p class="text-lg font-semibold mt-0.5 ${i.color}">${i.value}</p>
    </div>
  `).join('');

  // 详细指标表（与回测结果页一致）
  const pf = m.profit_factor == null ? '∞' : Fmt.num(m.profit_factor, 2);
  const rows = [
    ['总收益率',      Fmt.pct(m.total_return), m.total_return >= 0 ? 'text-red-400' : 'text-green-400'],
    ['年化收益率',    Fmt.pct(m.annual_return), m.annual_return >= 0 ? 'text-red-400' : 'text-green-400'],
    ['最大回撤',      Fmt.pct(m.max_drawdown), 'text-green-400'],
    ['回撤区间',      (m.max_drawdown_start && m.max_drawdown_end) ? `${m.max_drawdown_start} → ${m.max_drawdown_end}` : '—', ''],
    ['年化波动率',    Fmt.pct(m.volatility, 2), ''],
    ['夏普比率',      Fmt.num(m.sharpe_ratio, 4), ''],
    ['索提诺比率',    Fmt.num(m.sortino_ratio, 4), ''],
    ['卡玛比率',      Fmt.num(m.calmar_ratio, 4), ''],
    ['胜率',          Fmt.pct(m.win_rate, 1), ''],
    ['平均盈利',      Fmt.pct(m.avg_profit, 2), 'text-red-400'],
    ['平均亏损',      Fmt.pct(m.avg_loss, 2), 'text-green-400'],
    ['盈亏比',        pf, ''],
    ['平均持仓天数',  (m.avg_hold_days || 0).toFixed(1) + ' 天', ''],
    ['总手续费',      Fmt.money(m.total_fees), ''],
  ];
  const detailEl = document.getElementById('detail-metrics');
  if (detailEl) {
    detailEl.innerHTML = rows.map(([label, value, cls]) => `
      <div class="flex justify-between py-1 border-b border-gray-800/50">
        <span class="text-gray-400">${label}</span>
        <span class="${cls} text-gray-200">${value}</span>
      </div>
    `).join('');
  }
}

function exportTradesCSV(trades) {
  const header = '交易ID,日期,代码,方向,价格,数量,金额,佣金,印花税,净额';
  const rows = trades.map(t => {
    const net = t.side === 'BUY'
      ? -(t.amount + (t.commission || 0))
      : (t.amount - (t.commission || 0) - (t.stamp_tax || 0));
    return [t.trade_id || '', t.trade_date, t.symbol, t.side === 'BUY' ? '买入' : '卖出',
      t.price, t.quantity, t.amount,
      (t.commission || 0).toFixed(2), (t.stamp_tax || 0).toFixed(2), net.toFixed(2)
    ].join(',');
  });
  const csv = '\uFEFF' + header + '\n' + rows.join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = `trades_${currentSessionId || 'export'}.csv`;
  a.click(); URL.revokeObjectURL(url);
}

// ── 月度收益 ─────────────────────────────────────────────────────────────────

function renderMonthlyReturns(equityArr) {
  if (!equityArr || equityArr.length < 2) return;
  showMetricsSection();

  const monthly = _computeMonthlyReturns(equityArr);
  if (!monthly.length) return;

  // 柱状图
  const barDom = document.getElementById('monthly-bar-chart');
  if (!barDom) return;
  const barChart = echarts.getInstanceByDom(barDom) || echarts.init(barDom, 'dark');
  barChart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 48, right: 12, top: 16, bottom: 48 },
    xAxis: {
      type: 'category', data: monthly.map(m => m.ym),
      axisLabel: { color: '#6b7280', fontSize: 9, rotate: 45 },
      axisLine: { lineStyle: { color: '#374151' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#6b7280', fontSize: 10, formatter: v => v.toFixed(1) + '%' },
      splitLine: { lineStyle: { color: '#1f2937' } },
    },
    tooltip: {
      trigger: 'axis', backgroundColor: '#1f2937', borderColor: '#374151',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: p => `<b>${p[0].name}</b>：${p[0].value >= 0 ? '+' : ''}${p[0].value.toFixed(2)}%`,
    },
    series: [{
      type: 'bar', barMaxWidth: 24,
      data: monthly.map(m => ({
        value: +(m.ret * 100).toFixed(2),
        itemStyle: { color: m.ret >= 0 ? '#ef4444' : '#22c55e', borderRadius: [2, 2, 0, 0] },
      })),
    }],
  });
  window.addEventListener('resize', () => barChart.resize());

  // 热力图
  _renderMonthlyHeatmap(monthly);
}

function _computeMonthlyReturns(equityArr) {
  if (!equityArr.length) return [];
  const lastOfMonth = {};
  equityArr.forEach(d => {
    const ym = d.date.slice(0, 7);
    lastOfMonth[ym] = d.total_assets;
  });
  const yms = Object.keys(lastOfMonth).sort();
  return yms.map((ym, j) => {
    const cur = lastOfMonth[ym];
    const prev = j === 0 ? equityArr[0].total_assets : lastOfMonth[yms[j - 1]];
    return { ym, ret: prev > 0 ? (cur - prev) / prev : 0 };
  });
}

function _renderMonthlyHeatmap(monthly) {
  const years = [...new Set(monthly.map(m => m.ym.slice(0, 4)))].sort();
  const dataMap = {};
  monthly.forEach(m => { dataMap[m.ym] = m.ret; });
  const months = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];

  function cellColor(ret) {
    if (ret === undefined || ret === null) return { bg: '#111827', text: '#4b5563' };
    if (ret >  0.05) return { bg: '#991b1b', text: '#fff' };
    if (ret >  0.02) return { bg: '#dc2626', text: '#fff' };
    if (ret >  0.005) return { bg: '#ef4444', text: '#fff' };
    if (ret >  0)    return { bg: '#fca5a5', text: '#7f1d1d' };
    if (ret === 0)   return { bg: '#1f2937', text: '#9ca3af' };
    if (ret > -0.005) return { bg: '#bbf7d0', text: '#14532d' };
    if (ret > -0.02) return { bg: '#22c55e', text: '#14532d' };
    if (ret > -0.05) return { bg: '#16a34a', text: '#fff' };
    return { bg: '#15803d', text: '#fff' };
  }

  const headerCells = months.map(m =>
    `<div style="text-align:center;color:#6b7280;font-size:10px;padding:2px 0">${m}</div>`
  ).join('');

  const dataRows = years.map(year => {
    const cells = Array.from({ length: 12 }, (_, i) => {
      const ym = `${year}-${String(i + 1).padStart(2, '0')}`;
      const ret = dataMap[ym];
      const { bg, text } = cellColor(ret);
      const label = ret !== undefined
        ? (ret >= 0 ? '+' : '') + (ret * 100).toFixed(1) + '%'
        : '';
      return `<div style="background:${bg};color:${text};border-radius:3px;padding:3px 2px;text-align:center;font-size:10px;white-space:nowrap" title="${ym} ${label}">${label}</div>`;
    }).join('');
    return `
      <div style="color:#9ca3af;font-size:10px;text-align:right;padding-right:6px;line-height:24px">${year}</div>
      ${cells}`;
  }).join('');

  document.getElementById('monthly-heatmap').innerHTML = `
    <div style="display:grid;grid-template-columns:36px repeat(12,1fr);gap:3px;min-width:520px">
      <div></div>${headerCells}
      ${dataRows}
    </div>`;
}

// ── 历史会话 ─────────────────────────────────────────────────────────────────

async function loadSessions() {
  try {
    const res = await fetch('/api/live/sessions');
    const data = await res.json();
    refreshSessionsList(data.sessions);
  } catch (e) {
    console.error('加载会话列表失败', e);
  }
}

function refreshSessionsList(sessionsOverride) {
  // 如果没有传入数据，用缓存的状态构造一个简易列表
  const list = document.getElementById('sessions-list');

  // 先从 API 加载完整列表，再合并活跃内存状态
  // 这里采用简洁方案：直接用传入的数据渲染
  Promise.resolve().then(async () => {
    let sessions;
    if (sessionsOverride) {
      sessions = sessionsOverride;
    } else {
      try {
        const res = await fetch('/api/live/sessions');
        const data = await res.json();
        sessions = data.sessions || [];
      } catch (e) { return; }
    }

    if (!sessions.length) {
      list.innerHTML = '<p class="text-sm text-gray-600">暂无记录</p>';
      return;
    }

    // 为活跃会话注入内存中的最新状态
    sessions = sessions.map(s => {
      const mem = _session(s.session_id);
      if (mem && mem.lastStatus) {
        return { ...s, status: mem.lastStatus.status, total_assets: mem.lastStatus.total_assets ?? s.total_assets, elapsed_seconds: mem.lastStatus.elapsed_seconds ?? s.elapsed_seconds };
      }
      return s;
    });

    list.innerHTML = sessions.map(s => {
      const statusMap = { starting: '启动中', running: '运行中', stopped: '已停止', failed: '异常' };
      const isActive = s.status === 'running' || s.status === 'starting';
      const dotColor = { starting: 'bg-yellow-500', running: 'bg-green-500 animate-pulse', stopped: 'bg-gray-500', failed: 'bg-red-500' };
      const modeLabel = s.mode === 'live' ? '实盘' : '模拟';
      const modeCls = s.mode === 'live'
        ? 'bg-red-900/50 text-red-400 border-red-800'
        : 'bg-blue-900/50 text-blue-400 border-blue-800';
      const activeBorder = s.session_id === currentSessionId ? 'border-blue-700' : 'border-gray-800';
      const elapsedCls = isActive ? 'text-green-400' : 'text-gray-500';
      return `<div class="flex items-center justify-between bg-gray-900 border ${activeBorder} rounded-lg px-4 py-3 cursor-pointer hover:border-gray-700 transition-colors"
                   onclick="reconnectSession('${s.session_id}')">
        <div class="flex items-center gap-3 min-w-0">
          <span class="w-2 h-2 rounded-full shrink-0 ${dotColor[s.status] || 'bg-gray-500'}"></span>
          <span class="px-1.5 py-0.5 text-[10px] font-medium rounded border shrink-0 ${modeCls}">${modeLabel}</span>
          <span class="text-sm text-gray-200 shrink-0">${s.strategy_id}</span>
          <span class="text-xs text-gray-500 truncate">${s.symbols.join(', ')}</span>
        </div>
        <div class="flex items-center gap-5 shrink-0">
          <span class="text-sm ${s.total_assets ? 'text-green-400' : 'text-gray-500'}">${Fmt.money(s.total_assets)}</span>
          <span class="text-xs text-gray-500" title="启动时间">${Fmt.datetime(s.started_at)}</span>
          <span class="text-xs ${elapsedCls}">${isActive ? '运行中' : Fmt.elapsed(s.elapsed_seconds)}</span>
          <span class="text-xs text-gray-500">${statusMap[s.status] || s.status}</span>
          <button onclick="event.stopPropagation(); deleteSession('${s.session_id}')"
            class="text-gray-600 hover:text-red-400 transition-colors text-xs px-1" title="删除">✕</button>
        </div>
      </div>`;
    }).join('');
  });
}

async function deleteSession(sessionId) {
  if (!confirm('确定删除此会话及其所有数据？')) return;
  try {
    const res = await fetch(`/api/live/${sessionId}`, { method: 'DELETE' });
    if (!res.ok) throw new Error('删除失败');
    await loadSessions();
  } catch (e) {
    console.error('删除失败', e);
  }
}

async function reconnectSession(sessionId) {
  currentSessionId = sessionId;
  const st = _ensure(sessionId);

  document.getElementById('start-btn').disabled = false;
  const stopBtn = document.getElementById('stop-btn');
  stopBtn.disabled = true;

  // 检查是否有缓存的运行状态
  const cached = st.lastStatus;
  const isRunning = cached && (cached.status === 'running' || cached.status === 'starting');

  // 先获取当前状态
  try {
    const res = await fetch(`/api/live/${sessionId}/status`);
    const data = await res.json();

    // 只有当前仍在运行的会话才启用停止按钮
    if (data.status === 'running' || data.status === 'starting') {
      stopBtn.disabled = false;
      // 如果还没有 SSE，启动一个
      if (!st.sse || st.sse.readyState === EventSource.CLOSED) {
        startSSE(sessionId);
      }
    }

    updateDashboard(data);

    // 加载净值曲线（优先用 status 中携带的完整数据）
    if (data.equity_curve && data.equity_curve.dates && data.equity_curve.dates.length > 0) {
      st.equityData = data.equity_curve.dates.map((d, i) => ({
        date: d,
        total_assets: data.equity_curve.values[i],
        cash: 0,
        drawdown: data.equity_curve.drawdown ? data.equity_curve.drawdown[i] : 0,
      }));
      initEquityChart();
      updateEquityChart(data.recent_trades, st.equityData);
      renderMonthlyReturns(st.equityData);
    } else if (data.status === 'running' || data.status === 'starting') {
      // 正在运行的会话没有完整数据，从 API 加载
      await loadEquityCurve(sessionId);
    }
  } catch (e) {
    console.error('重连失败', e);
  }
}

// ── 净值曲线 ─────────────────────────────────────────────────────────────────

function initEquityChart() {
  const dom = document.getElementById('equity-chart');
  if (!dom) return;
  if (!equityChart) {
    equityChart = echarts.init(dom, 'dark');
    window.addEventListener('resize', () => equityChart?.resize());
  }
}

function updateEquityChart(trades, eqData) {
  if (!eqData) eqData = _cur()?.equityData || [];
  if (eqData.length === 0) return;
  document.getElementById('equity-section').classList.remove('hidden');
  initEquityChart();
  equityChart.resize();

  const dates = eqData.map(d => d.date);
  const assets = eqData.map(d => d.total_assets);
  const initialCapital = assets[0] || 1000000;

  // 是否有回撤数据
  const hasDrawdown = eqData.some(d => d.drawdown !== undefined && d.drawdown !== null);
  const grid = hasDrawdown
    ? [
        { left: 60, right: 16, top: 40, height: '55%' },
        { left: 60, right: 16, top: '68%', height: '18%' },
      ]
    : [{ left: 60, right: 16, top: 40, bottom: 50 }];

  const yAxis = hasDrawdown
    ? [
        { type: 'value', axisLabel: { color: '#6b7280', formatter: v => (v / 10000).toFixed(0) + '万' }, splitLine: { lineStyle: { color: '#1f2937' } } },
        { type: 'value', gridIndex: 1, axisLabel: { color: '#6b7280', formatter: v => (v * 100).toFixed(0) + '%' }, splitLine: { lineStyle: { color: '#1f2937' } } },
      ]
    : [{ type: 'value', axisLabel: { color: '#6b7280', formatter: v => (v / 10000).toFixed(0) + '万' }, splitLine: { lineStyle: { color: '#1f2937' } } }];

  const dataZoom = (() => {
    const defaultStart = dates.length > 200 ? Math.round((1 - 200 / dates.length) * 100) : 0;
    const zooms = [
      { type: 'inside', xAxisIndex: 0, start: defaultStart, end: 100 },
      { type: 'slider', xAxisIndex: 0, bottom: hasDrawdown ? '13%' : 8, height: 20,
        start: defaultStart, end: 100,
        borderColor: '#374151', fillerColor: 'rgba(59,130,246,0.15)',
        textStyle: { color: '#6b7280', fontSize: 10 } },
    ];
    return zooms;
  })();

  const xAxis = hasDrawdown
    ? [
        { type: 'category', data: dates, boundaryGap: false, gridIndex: 0, axisLabel: { show: false } },
        { type: 'category', data: dates, boundaryGap: false, gridIndex: 1, axisLabel: { color: '#6b7280', fontSize: 10, rotate: 30, interval: Math.max(0, Math.floor(dates.length / 10) - 1) } },
      ]
    : { type: 'category', data: dates, boundaryGap: false, axisLabel: { color: '#6b7280', fontSize: 10, rotate: 30, interval: Math.max(0, Math.floor(dates.length / 10) - 1) } };

  // 买卖标记：红▲买入、绿▼卖出（A股习惯），hover 显示详情
  const markPoint = hasDrawdown && trades && trades.length > 0 ? (() => {
    const dateToVal = {};
    dates.forEach((d, i) => { dateToVal[d] = assets[i]; });
    const nameMap = {};
    (allSymbols || []).forEach(s => { nameMap[s.symbol] = s.name || ''; });
    const markers = trades.map(t => {
      const y = dateToVal[t.trade_date];
      if (y === undefined) return null;
      const isBuy = t.side === 'BUY';
      const sName = nameMap[t.symbol] || '';
      const label = (isBuy ? '买入 ' : '卖出 ') + t.symbol + (sName ? ' ' + sName : '') + '\n' + t.quantity + '股 @' + Fmt.price(t.price) + ' ' + Fmt.money(t.amount);
      return {
        coord: [t.trade_date, y],
        name: label,
        symbol: 'triangle',
        symbolRotate: isBuy ? 0 : 180,
        symbolSize: 16,
        itemStyle: { color: isBuy ? '#ef4444' : '#22c55e', borderWidth: 0 },
        emphasis: { label: { show: true, formatter: label.replace(/\n/g, ' '), position: 'top', fontSize: 11, color: '#e5e7eb', backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, padding: [4, 8], borderRadius: 4 } },
      };
    }).filter(Boolean);
    return { symbolSize: 14, label: { show: false }, emphasis: { label: { show: true } }, data: markers };
  })() : undefined;

  const series = [
    { name: '总资产', type: 'line', data: assets, smooth: true, symbol: 'none', xAxisIndex: 0, yAxisIndex: 0,
      lineStyle: { width: 2 }, itemStyle: { color: '#34d399' },
      markPoint,
    },
    { name: '初始资金', type: 'line', data: dates.map(() => initialCapital), symbol: 'none', xAxisIndex: 0, yAxisIndex: 0,
      lineStyle: { width: 1, type: 'dashed', color: '#6b7280' }, itemStyle: { color: '#6b7280' },
    },
  ];

  if (hasDrawdown) {
    const dd = eqData.map(d => d.drawdown || 0);
    series.push({
      name: '回撤', type: 'line', data: dd, smooth: true, symbol: 'none', xAxisIndex: 1, yAxisIndex: 1,
      lineStyle: { width: 1 }, itemStyle: { color: '#f87171' },
      areaStyle: { color: 'rgba(248,113,113,0.15)' },
    });
  }

  equityChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        let s = `<b>${params[0].axisValue}</b><br/>`;
        params.forEach(p => {
          if (p.seriesName === '回撤') {
            s += `${p.marker} ${p.seriesName}: ${(p.value * 100).toFixed(2)}%<br/>`;
          } else {
            s += `${p.marker} ${p.seriesName}: ${Fmt.money(p.value)}<br/>`;
          }
        });
        const asset = params.find(p => p.seriesName === '总资产');
        const base = params.find(p => p.seriesName === '初始资金');
        if (asset && base && base.value > 0) {
          const ret = ((asset.value - base.value) / base.value * 100).toFixed(2);
          const color = ret >= 0 ? '#f87171' : '#34d399';
          s += `<span style="color:${color}">收益率: ${ret}%</span>`;
        }
        return s;
      },
    },
    legend: { data: hasDrawdown ? ['总资产', '初始资金', '回撤'] : ['总资产', '初始资金'], top: 8, textStyle: { color: '#9ca3af' } },
    grid, xAxis, yAxis, dataZoom, series,
  });
}

async function loadEquityCurve(sessionId) {
  try {
    const res = await fetch(`/api/live/${sessionId}/equity`);
    const data = await res.json();
    if (data.length > 0) {
      const st = _ensure(sessionId);
      st.equityData = data.map(d => ({ date: d.trade_date, total_assets: d.total_assets, cash: d.cash }));
      initEquityChart();
      updateEquityChart(null, st.equityData);
    }
  } catch (e) {
    console.error('加载净值曲线失败', e);
  }
}
