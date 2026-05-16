/**
 * 模拟盘页面交互逻辑
 */

let strategies = [];
let allSymbols = [];
let selectedSymbols = new Set();
let currentSessionId = null;
let sseSource = null;
let equityChart = null;
let equityData = [];  // { date, total_assets, cash }
let currentMode = 'paper'; // "paper" | "live"

const Fmt = {
  money: v => v == null ? '--' : v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
  pct: v => v == null ? '--' : (v * 100).toFixed(2) + '%',
  price: v => v == null ? '--' : v.toFixed(2),
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

async function loadAllSymbols() {
  try {
    const res = await fetch('/api/symbols?sync=false');
    const data = await res.json();
    allSymbols = data.symbols || [];
  } catch (e) {
    console.error('加载股票池失败', e);
  }
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
  const stopBtn = document.getElementById('stop-btn');
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
    currentSessionId = data.session_id;
    stopBtn.disabled = false;

    equityData = [];
    showStatusSection();
    startSSE(currentSessionId);

  } catch (e) {
    showError(e.message);
    startBtn.disabled = false;
  }
}

function showError(msg) {
  const el = document.getElementById('form-error');
  el.textContent = msg;
  el.classList.remove('hidden');
}

// ── SSE 实时推送 ─────────────────────────────────────────────────────────────

function startSSE(sessionId) {
  if (sseSource) sseSource.close();
  sseSource = new EventSource(`/api/live/${sessionId}/stream`);

  sseSource.addEventListener('status', e => {
    const data = JSON.parse(e.data);
    updateDashboard(data);
  });

  sseSource.addEventListener('stopped', e => {
    const data = JSON.parse(e.data);
    updateDashboard(data);
    onSessionEnd('stopped');
  });

  sseSource.addEventListener('error', e => {
    if (e.data) {
      const data = JSON.parse(e.data);
      showError(data.error || '会话异常');
    }
    onSessionEnd('failed');
  });

  sseSource.onerror = () => {
    // SSE 连接断开，回退到轮询
    sseSource.close();
    pollStatus(sessionId);
  };
}

async function pollStatus(sessionId) {
  while (true) {
    await sleep(1000);
    try {
      const res = await fetch(`/api/live/${sessionId}/status`);
      const data = await res.json();
      updateDashboard(data);
      if (data.status === 'stopped' || data.status === 'failed') {
        onSessionEnd(data.status);
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

function onSessionEnd(status) {
  if (sseSource) sseSource.close();
  sseSource = null;
  document.getElementById('start-btn').disabled = false;
  document.getElementById('stop-btn').disabled = true;

  const label = document.getElementById('status-label');
  label.textContent = status === 'stopped' ? '已停止' : '异常';
  label.className = 'text-sm font-semibold ' + (status === 'stopped' ? 'text-gray-400' : 'text-red-400');

  loadSessions();
}

// ── 仪表盘更新 ───────────────────────────────────────────────────────────────

function showStatusSection() {
  document.getElementById('status-section').classList.remove('hidden');
  document.getElementById('positions-section').classList.remove('hidden');
  document.getElementById('trades-section').classList.remove('hidden');
  // 折叠配置区，让图表更醒目
  document.getElementById('config-section').removeAttribute('open');
}

function updateDashboard(data) {
  showStatusSection();

  // 追加净值数据点（按日期去重）
  if (data.current_date && data.total_assets != null) {
    const last = equityData[equityData.length - 1];
    if (!last || last.date !== data.current_date) {
      equityData.push({ date: data.current_date, total_assets: data.total_assets, cash: data.cash || 0 });
      initEquityChart();
      updateEquityChart();
    } else {
      last.total_assets = data.total_assets;
      last.cash = data.cash || 0;
      updateEquityChart();
    }
  }

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
  if (data.recent_trades && data.recent_trades.length > 0) {
    tradeBody.innerHTML = data.recent_trades.map(t => {
      const sideColor = t.side === 'BUY' ? 'text-red-400' : 'text-green-400';
      const sideText = t.side === 'BUY' ? '买入' : '卖出';
      return `<tr class="hover:bg-gray-800/50">
        <td class="px-3 py-2 text-left text-gray-400">${t.trade_date}</td>
        <td class="px-3 py-2 text-left text-gray-200">${t.symbol}</td>
        <td class="px-3 py-2 text-center ${sideColor}">${sideText}</td>
        <td class="px-3 py-2 text-right">${Fmt.price(t.price)}</td>
        <td class="px-3 py-2 text-right">${t.quantity}</td>
        <td class="px-3 py-2 text-right">${Fmt.money(t.amount)}</td>
      </tr>`;
    }).join('');
  } else {
    tradeBody.innerHTML = '<tr><td colspan="6" class="px-3 py-4 text-center text-gray-600">暂无成交</td></tr>';
  }
}

// ── 历史会话 ─────────────────────────────────────────────────────────────────

async function loadSessions() {
  try {
    const res = await fetch('/api/live/sessions');
    const data = await res.json();
    const list = document.getElementById('sessions-list');

    if (!data.sessions || data.sessions.length === 0) {
      list.innerHTML = '<p class="text-sm text-gray-600">暂无记录</p>';
      return;
    }

    list.innerHTML = data.sessions.map(s => {
      const statusMap = { starting: '启动中', running: '运行中', stopped: '已停止', failed: '异常' };
      const colorMap = { starting: 'bg-yellow-500', running: 'bg-green-500', stopped: 'bg-gray-500', failed: 'bg-red-500' };
      const modeLabel = s.mode === 'live' ? '实盘' : '模拟';
      const modeCls = s.mode === 'live'
        ? 'bg-red-900/50 text-red-400 border-red-800'
        : 'bg-blue-900/50 text-blue-400 border-blue-800';
      return `<div class="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 cursor-pointer hover:border-gray-700 transition-colors"
                   onclick="reconnectSession('${s.session_id}')">
        <div class="flex items-center gap-3">
          <span class="w-2 h-2 rounded-full ${colorMap[s.status] || 'bg-gray-500'}"></span>
          <span class="px-1.5 py-0.5 text-[10px] font-medium rounded border ${modeCls}">${modeLabel}</span>
          <span class="text-sm text-gray-200">${s.strategy_id}</span>
          <span class="text-xs text-gray-500">${s.symbols.join(', ')}</span>
        </div>
        <div class="flex items-center gap-4">
          <span class="text-sm ${s.total_assets ? 'text-green-400' : 'text-gray-500'}">${Fmt.money(s.total_assets)}</span>
          <span class="text-xs text-gray-500">${statusMap[s.status] || s.status}</span>
          <button onclick="event.stopPropagation(); deleteSession('${s.session_id}')"
            class="text-gray-600 hover:text-red-400 transition-colors text-xs px-1" title="删除">✕</button>
        </div>
      </div>`;
    }).join('');
  } catch (e) {
    console.error('加载会话列表失败', e);
  }
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
  document.getElementById('stop-btn').disabled = false;
  document.getElementById('start-btn').disabled = true;

  // 先获取一次当前状态
  try {
    const res = await fetch(`/api/live/${sessionId}/status`);
    const data = await res.json();
    updateDashboard(data);

    if (data.status === 'running' || data.status === 'starting') {
      startSSE(sessionId);
    } else {
      onSessionEnd(data.status);
    }

    // 加载历史净值曲线
    await loadEquityCurve(sessionId);
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

function updateEquityChart() {
  if (equityData.length === 0) return;
  // 先显示容器，再初始化/resize（否则 ECharts 拿不到尺寸）
  document.getElementById('equity-section').classList.remove('hidden');
  initEquityChart();
  equityChart.resize();

  const dates = equityData.map(d => d.date);
  const assets = equityData.map(d => d.total_assets);

  // 初始资金基准线
  const initialCapital = assets[0] || 1000000;

  equityChart.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: params => {
        let s = `<b>${params[0].axisValue}</b><br/>`;
        params.forEach(p => {
          s += `${p.marker} ${p.seriesName}: ${Fmt.money(p.value)}<br/>`;
        });
        // 追加收益率
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
    legend: { data: ['总资产', '初始资金'], top: 8, textStyle: { color: '#9ca3af' } },
    grid: { top: 40, right: 16, bottom: 50, left: 60 },
    dataZoom: (() => {
      // 数据量大时默认只显示最近200个交易日，少于200则全量
      const defaultStart = dates.length > 200 ? Math.round((1 - 200 / dates.length) * 100) : 0;
      return [
        { type: 'inside', xAxisIndex: 0, start: defaultStart, end: 100 },
        { type: 'slider', xAxisIndex: 0, bottom: 8, height: 20,
          start: defaultStart, end: 100,
          borderColor: '#374151', fillerColor: 'rgba(59,130,246,0.15)',
          textStyle: { color: '#6b7280', fontSize: 10 } },
      ];
    })(),
    xAxis: {
      type: 'category', data: dates, boundaryGap: false,
      axisLabel: { color: '#6b7280', fontSize: 10, rotate: 30,
        interval: Math.max(0, Math.floor(dates.length / 10) - 1) },
    },
    yAxis: {
      type: 'value',
      min: function(value) { var pad = (value.max - value.min) * 0.1; return Math.max(0, Math.floor((value.min - pad) / 10000) * 10000); },
      max: function(value) { var pad = (value.max - value.min) * 0.1; return Math.ceil((value.max + pad) / 10000) * 10000; },
      axisLabel: { color: '#6b7280', formatter: v => (v / 10000).toFixed(0) + '万' },
      splitLine: { lineStyle: { color: '#1f2937' } },
    },
    series: [
      { name: '总资产', type: 'line', data: assets, smooth: true, symbol: 'none',
        lineStyle: { width: 2 }, itemStyle: { color: '#34d399' },
      },
      { name: '初始资金', type: 'line', data: dates.map(() => initialCapital), symbol: 'none',
        lineStyle: { width: 1, type: 'dashed', color: '#6b7280' }, itemStyle: { color: '#6b7280' },
      },
    ],
  });
}

async function loadEquityCurve(sessionId) {
  try {
    const res = await fetch(`/api/live/${sessionId}/equity`);
    const data = await res.json();
    if (data.length > 0) {
      equityData = data.map(d => ({ date: d.trade_date, total_assets: d.total_assets, cash: d.cash }));
      initEquityChart();
      updateEquityChart();
    }
  } catch (e) {
    console.error('加载净值曲线失败', e);
  }
}
