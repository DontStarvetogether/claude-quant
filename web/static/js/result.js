/**
 * 结果页：图表渲染 + 指标展示 + 成交记录
 */

// 股票代码 → 公司名映射（从后端动态获取）
let STOCK_NAMES = {};
let stockNamesLoaded = false;

// 从后端获取股票名称映射
async function loadStockNames() {
  if (stockNamesLoaded) return;
  
  try {
    const data = await API.getSymbols();
    STOCK_NAMES = Object.fromEntries(
      data.symbols.map(s => [s.symbol, s.name])
    );
    stockNamesLoaded = true;
    console.log(`已加载 ${Object.keys(STOCK_NAMES).length} 只股票名称`);
  } catch (e) {
    console.error('加载股票名称失败', e);
  }
}

let resultData = null;

document.addEventListener('DOMContentLoaded', async () => {
  const params = new URLSearchParams(window.location.search);
  const runId = params.get('run_id');

  if (!runId) {
    document.getElementById('loading').textContent = '缺少 run_id 参数';
    return;
  }

  try {
    // 先加载股票名称映射
    await loadStockNames();
    
    resultData = await API.getResult(runId);
    renderPage(resultData);

  } catch (e) {
    document.getElementById('loading').textContent = '加载失败：' + e.message;
  }
});

// ── 主渲染 ────────────────────────────────────────────────────────────────────
function renderPage(data) {
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('main-content').classList.remove('hidden');

  // Header：显示股票代码+公司名
  document.getElementById('header-strategy').textContent = data.strategy_name;
  document.getElementById('header-symbols').textContent = data.symbols
    .map(s => STOCK_NAMES[s] ? `${s}(${STOCK_NAMES[s]})` : s)
    .join(', ');
  document.getElementById('header-dates').textContent = `${data.start_date} → ${data.end_date}`;
  document.title = `${data.strategy_name} 回测结果 — Claude Quant`;

  renderMetricCards(data.metrics);
  renderEquityChart(data.equity_curve, data.metrics, data.trades);
  renderMonthlyReturns(data.equity_curve);
  renderDetailMetrics(data.metrics);
  renderTradesTable(data.trades);

  document.getElementById('export-csv-btn').addEventListener('click', () => exportCsv(data.trades));
}

// ── 指标卡片 ──────────────────────────────────────────────────────────────────
function renderMetricCards(m) {
  const set = (id, value, colorFn) => {
    const el = document.getElementById(id);
    el.textContent = value;
    if (colorFn) {
      el.className = el.className.replace(/positive|negative/g, '');
      el.classList.add(colorFn);
    }
  };

  const totalRet = m.total_return;
  set('m-total-return', Fmt.pct(totalRet), Fmt.colorClass(totalRet));
  set('m-annual-return', Fmt.pct(m.annual_return), Fmt.colorClass(m.annual_return));
  set('m-max-drawdown', Fmt.pct(m.max_drawdown), 'negative');
  set('m-sharpe', Fmt.num(m.sharpe_ratio, 3));
  set('m-win-rate', Fmt.pct(m.win_rate, 1));
  set('m-total-trades', m.total_trades + ' 笔');
  set('m-final-value', Fmt.money(m.final_value));
  
  // 显示持有现金和持仓市值的拆分
  if (m.final_cash !== undefined && m.final_position_value !== undefined) {
    const breakdown = `现金 ${Fmt.money(m.final_cash)} + 持仓 ${Fmt.money(m.final_position_value)}`;
    set('m-final-breakdown', breakdown);
  }
  
  set('m-total-fees', Fmt.money(m.total_fees));
  
  // 显示佣金和印花税的拆分
  if (m.total_commission !== undefined && m.total_stamp_tax !== undefined) {
    const feesBreakdown = `佣金 ${Fmt.money(m.total_commission)} + 印花税 ${Fmt.money(m.total_stamp_tax)}`;
    set('m-fees-breakdown', feesBreakdown);
  }
}

// ── 权益曲线图 ─────────────────────────────────────────────────────────────────
function renderEquityChart(curve, metrics, trades = []) {
  const chart = echarts.init(document.getElementById('equity-chart'), null, { renderer: 'canvas' });

  const ddStart = metrics.max_drawdown_start;
  const ddEnd = metrics.max_drawdown_end;
  const markArea = ddStart && ddEnd ? {
    silent: true,
    itemStyle: { color: 'rgba(239,68,68,0.06)' },
    data: [[{ xAxis: ddStart }, { xAxis: ddEnd }]],
  } : {};

  const option = {
    backgroundColor: 'transparent',
    grid: [
      { left: 60, right: 60, top: 20, bottom: 80, height: '55%' },
      { left: 60, right: 60, top: '68%', bottom: 50 },
    ],
    xAxis: [
      { type: 'category', data: curve.dates, gridIndex: 0, axisLine: { lineStyle: { color: '#374151' } }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { show: false } },
      { type: 'category', data: curve.dates, gridIndex: 1, axisLine: { lineStyle: { color: '#374151' } }, axisLabel: { color: '#6b7280', fontSize: 10 }, splitLine: { show: false } },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, axisLabel: { color: '#6b7280', fontSize: 10, formatter: v => (v / 10000).toFixed(0) + '万' }, splitLine: { lineStyle: { color: '#1f2937' } } },
      { type: 'value', gridIndex: 1, max: 0, axisLabel: { color: '#6b7280', fontSize: 10, formatter: v => v.toFixed(1) + '%' }, splitLine: { lineStyle: { color: '#1f2937' } } },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross', lineStyle: { color: '#4b5563' } },
      backgroundColor: '#1f2937',
      borderColor: '#374151',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter(params) {
        const date = params[0]?.axisValue;
        let html = `<div style="font-weight:600;margin-bottom:4px">${date}</div>`;
        params.forEach(p => {
          const val = p.seriesName === '净资产'
            ? Fmt.money(p.value)
            : p.value.toFixed(2) + '%';
          html += `<div>${p.marker}${p.seriesName}: <b>${val}</b></div>`;
        });
        return html;
      },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
      { type: 'slider', xAxisIndex: [0, 1], start: 0, end: 100, height: 20, bottom: 8, textStyle: { color: '#6b7280' }, borderColor: '#374151', fillerColor: 'rgba(59,130,246,0.15)', handleStyle: { color: '#3b82f6' } },
    ],
    series: [
      {
        name: '净资产',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: curve.values,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#3b82f6', width: 1.5 },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [{ offset: 0, color: 'rgba(59,130,246,0.2)' }, { offset: 1, color: 'rgba(59,130,246,0)' }] } },
        markArea,
        markPoint: {
          symbolSize: 14,
          label: { show: false },
          data: _buildTradeMarkers(trades, curve),
        },
      },
      {
        name: '回撤%',
        type: 'line',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: curve.drawdown,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#ef4444', width: 1 },
        areaStyle: { color: 'rgba(239,68,68,0.15)' },
      },
    ],
  };

  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

// ── 详细指标 ──────────────────────────────────────────────────────────────────
function renderDetailMetrics(m) {
  const pf = m.profit_factor == null ? '∞' : Fmt.num(m.profit_factor, 2);
  const rows = [
    ['总收益率', Fmt.pct(m.total_return), Fmt.colorClass(m.total_return)],
    ['年化收益率', Fmt.pct(m.annual_return), Fmt.colorClass(m.annual_return)],
    ['最大回撤', Fmt.pct(m.max_drawdown), 'negative'],
    ['回撤区间', m.max_drawdown_start ? `${m.max_drawdown_start} → ${m.max_drawdown_end}` : '—', ''],
    ['年化波动率', Fmt.pct(m.volatility, 2), ''],
    ['夏普比率', Fmt.num(m.sharpe_ratio, 4), ''],
    ['索提诺比率', Fmt.num(m.sortino_ratio, 4), ''],
    ['卡玛比率', Fmt.num(m.calmar_ratio, 4), ''],
    ['胜率', Fmt.pct(m.win_rate, 1), ''],
    ['平均盈利', Fmt.pct(m.avg_profit, 2), 'positive'],
    ['平均亏损', Fmt.pct(m.avg_loss, 2), 'negative'],
    ['盈亏比', pf, ''],
    ['平均持仓天数', (m.avg_hold_days || 0).toFixed(1) + ' 天', ''],
    ['总手续费', Fmt.money(m.total_fees), ''],
  ];

  document.getElementById('detail-metrics').innerHTML = rows.map(([label, value, cls]) => `
    <div class="metric-row">
      <span class="metric-row-label">${label}</span>
      <span class="metric-row-value ${cls}">${value}</span>
    </div>
  `).join('');
}

// ── 成交记录 ──────────────────────────────────────────────────────────────────
function renderTradesTable(trades) {
  const tbody = document.getElementById('trades-tbody');

  if (!trades.length) {
    tbody.innerHTML = '<tr><td colspan="10" class="text-center text-gray-600 py-4">无成交记录</td></tr>';
    return;
  }

  tbody.innerHTML = trades.map(t => {
    const isBuy = t.side === 'BUY';
    const rowClass = isBuy ? 'trade-buy' : 'trade-sell';
    const sideColor = isBuy ? 'text-green-400' : 'text-red-400';
    const companyName = STOCK_NAMES[t.symbol] || '';
    
    return `
      <tr class="${rowClass} hover:bg-gray-800/30 transition-colors">
        <td class="py-1.5 text-gray-500 font-mono text-xs">${t.trade_id.substring(0, 8)}</td>
        <td class="py-1.5 text-gray-400">${t.trade_date}</td>
        <td class="${sideColor} font-medium">${isBuy ? '买入' : '卖出'}</td>
        <td>
          <div class="text-gray-300">${t.symbol}</div>
          ${companyName ? `<div class="text-gray-500 text-xs">${companyName}</div>` : ''}
        </td>
        <td class="text-right text-gray-200">${t.price.toFixed(2)}</td>
        <td class="text-right text-gray-400">${t.quantity}</td>
        <td class="text-right text-gray-300">${t.amount.toFixed(2)}</td>
        <td class="text-right text-yellow-600">${t.commission.toFixed(2)}</td>
        <td class="text-right text-yellow-600">${t.stamp_tax.toFixed(2)}</td>
        <td class="text-right ${isBuy ? 'text-red-400' : 'text-green-400'} font-medium">${t.net_amount.toFixed(2)}</td>
        <td class="text-right text-blue-400 font-medium">${t.cash_after.toFixed(2)}</td>
      </tr>
    `;
  }).join('');

  document.getElementById('trades-summary').textContent =
    `共 ${trades.length} 笔成交，` +
    `买入 ${trades.filter(t => t.side === 'BUY').length} 笔，` +
    `卖出 ${trades.filter(t => t.side === 'SELL').length} 笔`;
}

// ── 买卖点标记 ────────────────────────────────────────────────────────────────
function _buildTradeMarkers(trades, curve) {
  const dateToVal = {};
  curve.dates.forEach((d, i) => { dateToVal[d] = curve.values[i]; });

  return trades.map(t => {
    const y = dateToVal[t.trade_date];
    if (y === undefined) return null;
    const isBuy = t.side === 'BUY';
    const sName = STOCK_NAMES[t.symbol] || '';
    const label = (isBuy ? '买入 ' : '卖出 ') + t.symbol + (sName ? ' ' + sName : '') + ' ' + t.quantity + '股 @' + t.price.toFixed(2) + ' ' + Fmt.money(t.amount);
    return {
      coord: [t.trade_date, y],
      name: label,
      symbol: 'triangle',
      symbolRotate: isBuy ? 0 : 180,
      itemStyle: { color: isBuy ? '#22c55e' : '#ef4444', borderWidth: 0 },
      emphasis: { label: { show: true, formatter: label, position: 'top', fontSize: 11, color: '#e5e7eb', backgroundColor: '#1f2937', borderColor: '#374151', borderWidth: 1, padding: [4, 8], borderRadius: 4 } },
    };
  }).filter(Boolean);
}

// ── 月度收益 ──────────────────────────────────────────────────────────────────
function renderMonthlyReturns(curve) {
  const monthly = _computeMonthlyReturns(curve);
  if (!monthly.length) return;

  // 柱状图
  const barChart = echarts.init(document.getElementById('monthly-bar-chart'), null, { renderer: 'canvas' });
  barChart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 48, right: 12, top: 16, bottom: 48 },
    xAxis: {
      type: 'category',
      data: monthly.map(m => m.ym),
      axisLabel: { color: '#6b7280', fontSize: 9, rotate: 45 },
      axisLine: { lineStyle: { color: '#374151' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#6b7280', fontSize: 10, formatter: v => v.toFixed(1) + '%' },
      splitLine: { lineStyle: { color: '#1f2937' } },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1f2937',
      borderColor: '#374151',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: p => `<b>${p[0].name}</b>：${p[0].value >= 0 ? '+' : ''}${p[0].value.toFixed(2)}%`,
    },
    series: [{
      type: 'bar',
      barMaxWidth: 24,
      data: monthly.map(m => ({
        value: +(m.ret * 100).toFixed(2),
        itemStyle: { color: m.ret >= 0 ? '#22c55e' : '#ef4444', borderRadius: [2, 2, 0, 0] },
      })),
    }],
  });
  window.addEventListener('resize', () => barChart.resize());

  // 热力图
  _renderMonthlyHeatmap(monthly);
}

function _computeMonthlyReturns(curve) {
  if (!curve.dates.length) return [];

  // 每月最后一个交易日的净值
  const lastOfMonth = {};
  curve.dates.forEach((d, i) => {
    const ym = d.slice(0, 7);
    lastOfMonth[ym] = curve.values[i];
  });

  const yms = Object.keys(lastOfMonth).sort();
  return yms.map((ym, j) => {
    const cur = lastOfMonth[ym];
    const prev = j === 0 ? curve.values[0] : lastOfMonth[yms[j - 1]];
    return { ym, ret: prev > 0 ? (cur - prev) / prev : 0 };
  });
}

function _renderMonthlyHeatmap(monthly) {
  // 提取年份列表和数据 map
  const years = [...new Set(monthly.map(m => m.ym.slice(0, 4)))].sort();
  const dataMap = {};
  monthly.forEach(m => { dataMap[m.ym] = m.ret; });

  const months = ['1月','2月','3月','4月','5月','6月','7月','8月','9月','10月','11月','12月'];

  function cellColor(ret) {
    if (ret === undefined || ret === null) return { bg: '#111827', text: '#4b5563' };
    if (ret >  0.05) return { bg: '#15803d', text: '#fff' };
    if (ret >  0.02) return { bg: '#16a34a', text: '#fff' };
    if (ret >  0.005) return { bg: '#22c55e', text: '#14532d' };
    if (ret >  0)    return { bg: '#bbf7d0', text: '#14532d' };
    if (ret === 0)   return { bg: '#1f2937', text: '#9ca3af' };
    if (ret > -0.005) return { bg: '#fca5a5', text: '#7f1d1d' };
    if (ret > -0.02) return { bg: '#ef4444', text: '#fff' };
    if (ret > -0.05) return { bg: '#dc2626', text: '#fff' };
    return { bg: '#991b1b', text: '#fff' };
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

// ── 导出 CSV ──────────────────────────────────────────────────────────────────
function exportCsv(trades) {
  const header = ['交易ID', '日期', '方向', '股票代码', '公司名称', '价格', '数量', '成交额', '佣金', '印花税', '净额', '持有现金'];
  const rows = trades.map(t => [
    t.trade_id,
    t.trade_date,
    t.side === 'BUY' ? '买入' : '卖出',
    t.symbol,
    STOCK_NAMES[t.symbol] || '',
    t.price,
    t.quantity,
    t.amount,
    t.commission,
    t.stamp_tax,
    t.net_amount,
    t.cash_after,
  ]);
  const csv = [header, ...rows].map(r => r.join(',')).join('\n');
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `trades_${resultData?.strategy_name}_${Date.now()}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
