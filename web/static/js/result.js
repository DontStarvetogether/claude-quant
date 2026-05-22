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
    await renderResultLoadFailure(runId, e);
  }
});

async function renderResultLoadFailure(runId, error) {
  const loading = document.getElementById('loading');
  let status = null;
  try {
    status = await API.getStatus(runId);
  } catch (_) {
    // status 也不可读时，保留原始 result 错误。
  }

  if (!status) {
    loading.textContent = '加载失败：' + error.message;
    return;
  }

  const statusLabel = {
    pending: '等待中',
    running: '运行中',
    failed: '失败',
    completed: '已完成',
  }[status.status] || status.status;
  const message = status.error || error.message || '暂无错误详情';
  const progress = Number.isFinite(Number(status.progress)) ? `${status.progress}%` : '—';

  loading.innerHTML = `
    <div class="max-w-2xl mx-auto text-left bg-gray-900 border border-gray-800 rounded-lg p-5">
      <div class="text-sm text-gray-500 mb-2">回测结果不可用</div>
      <div class="text-xl font-semibold text-gray-100 mb-4">${escapeHtml(statusLabel)}</div>
      <div class="space-y-2 text-sm">
        <div><span class="text-gray-500">Run ID：</span><span class="font-mono text-gray-300">${escapeHtml(runId)}</span></div>
        <div><span class="text-gray-500">进度：</span><span class="text-gray-300">${escapeHtml(progress)}</span></div>
        <div><span class="text-gray-500">当前阶段：</span><span class="text-gray-300">${escapeHtml(status.current_date || '—')}</span></div>
        <div><span class="text-gray-500">耗时：</span><span class="text-gray-300">${Fmt.num(status.elapsed_seconds || 0, 1)} 秒</span></div>
        <div class="pt-2">
          <div class="text-gray-500 mb-1">错误信息</div>
          <pre class="whitespace-pre-wrap rounded bg-gray-950 border border-gray-800 p-3 text-yellow-300 text-xs">${escapeHtml(message)}</pre>
        </div>
      </div>
      <div class="mt-5">
        <a href="/" class="btn-secondary inline-block">返回首页</a>
      </div>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── 主渲染 ────────────────────────────────────────────────────────────────────
function renderPage(data) {
  document.getElementById('loading').classList.add('hidden');
  document.getElementById('main-content').classList.remove('hidden');

  // Header：截断过长股票列表
  const symCount = data.symbols.length;
  const symDisplay = symCount > 5
    ? data.symbols.slice(0, 5).map(s => STOCK_NAMES[s] ? `${s}(${STOCK_NAMES[s]})` : s).join(', ') + ` ，等 ${symCount} 只`
    : data.symbols.map(s => STOCK_NAMES[s] ? `${s}(${STOCK_NAMES[s]})` : s).join(', ');
  document.getElementById('header-strategy').textContent = data.strategy_name;
  document.getElementById('header-symbols').textContent = symDisplay;
  document.getElementById('header-dates').innerHTML = `${data.start_date} → ${data.end_date}` +
    (data.rejected_count ? ` &nbsp;<span class="text-xs text-gray-500">拒单 ${data.rejected_count} 次</span>` : '');
  document.title = `${data.strategy_name} 回测结果 — Claude Quant`;

  renderMetricCards(data.metrics, data);
  renderStrategyAssessment(data);
  renderAlphaBreakdown(data);
  renderEquityChart(data.equity_curve, data.metrics, data.trades, data.benchmark_curve, data.benchmark_name || data.benchmark);
  renderMonthlyReturns(data.equity_curve);
  renderDetailMetrics(data.metrics, data);
  renderTradesTable(data.trades);

  document.getElementById('export-csv-btn').addEventListener('click', () => exportCsv(data.trades));
}

// ── 指标卡片 ──────────────────────────────────────────────────────────────────
function renderMetricCards(m, data) {
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

  // 基准对比
  const benchmarkSelected = data?.benchmark != null && data.benchmark !== '';
  const hasBenchmark = benchmarkSelected && data?.alpha_beta_available === true;
  const hasBenchmarkCurve = benchmarkSelected && data?.benchmark_curve_available === true;
  const benchCards = document.querySelectorAll('.benchmark-only');
  benchCards.forEach(c => c.classList.toggle('hidden', !hasBenchmark));
  const missingCard = document.querySelector('.benchmark-missing');
  missingCard?.classList.toggle('hidden', !benchmarkSelected || hasBenchmark);
  if (hasBenchmark) {
    const bmName = data.benchmark_name || benchmarkName(data.benchmark);
    set('m-excess-return', Fmt.pct(m.excess_return), m.excess_return >= 0 ? 'positive' : 'negative');
    set('m-benchmark-info', `基准 ${bmName}: ${Fmt.pct(m.benchmark_return)}`);
    set('m-alpha', Fmt.num(m.alpha, 4), m.alpha >= 0 ? 'positive' : 'negative');
    set('m-beta', Fmt.num(m.beta, 4));
  } else if (benchmarkSelected) {
    const bmName = data.benchmark_name || benchmarkName(data.benchmark);
    const reason = data.benchmark_error || benchmarkStatusText(data.benchmark_status, hasBenchmarkCurve);
    set('m-benchmark-missing', `${bmName}: ${reason}`);
  }
}

function benchmarkStatusText(status, hasCurve) {
  if (status === 'not_requested') return '未选择基准';
  if (status === 'available') return hasCurve ? '基准曲线可用' : '基准指标可用，曲线缺失';
  return '未计算 Alpha/Beta';
}

// ── Alpha 拆解 ────────────────────────────────────────────────────────────────
function renderAlphaBreakdown(data) {
  const section = document.getElementById('alpha-breakdown');
  const body = document.getElementById('alpha-breakdown-body');
  const label = document.getElementById('alpha-benchmark-label');
  const chartEl = document.getElementById('relative-strength-chart');
  if (!section || !body || !label || !chartEl) return;

  const benchmarkSelected = data?.benchmark != null && data.benchmark !== '';
  if (!benchmarkSelected) {
    section.classList.add('hidden');
    return;
  }

  section.classList.remove('hidden');
  const bmName = data.benchmark_name || benchmarkName(data.benchmark);
  label.textContent = `${bmName} / ${data.benchmark}`;

  if (!data.alpha_beta_available || !data.benchmark_curve_available) {
    const reason = data.benchmark_error || benchmarkStatusText(data.benchmark_status, data.benchmark_curve_available);
    body.innerHTML = `
      <div class="rounded-lg border border-yellow-800/60 bg-yellow-950/20 px-4 py-3 text-sm text-yellow-300">
        ${escapeHtml(bmName)}: ${escapeHtml(reason)}
      </div>
    `;
    chartEl.classList.add('hidden');
    return;
  }

  chartEl.classList.remove('hidden');
  const m = data.metrics;
  const stats = computeBenchmarkBreakdown(data.equity_curve, data.benchmark_curve);
  const alphaClass = m.alpha >= 0 ? 'text-red-400' : 'text-green-400';
  const excessClass = m.excess_return >= 0 ? 'text-red-400' : 'text-green-400';
  const relativeClass = stats.relativeFinal >= 0 ? 'text-red-400' : 'text-green-400';
  const hitRateClass = stats.hitRate >= 0.5 ? 'text-red-400' : 'text-gray-200';

  body.innerHTML = `
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
      ${alphaMetric('策略收益', Fmt.pct(m.total_return), Fmt.colorClass(m.total_return), '绝对收益')}
      ${alphaMetric('基准收益', Fmt.pct(m.benchmark_return), Fmt.colorClass(m.benchmark_return), bmName)}
      ${alphaMetric('超额收益', Fmt.pct(m.excess_return), excessClass, '策略 - 基准')}
      ${alphaMetric('Jensen Alpha', Fmt.num(m.alpha, 4), alphaClass, 'Beta 调整后')}
      ${alphaMetric('Beta', Fmt.num(m.beta, 4), '', betaHint(m.beta))}
      ${alphaMetric('跑赢交易日', `${stats.winDays}/${stats.sampleDays}`, hitRateClass, pctPlain(stats.hitRate, 1))}
      ${alphaMetric('日均超额', Fmt.pct(stats.avgDailyExcess, 3), Fmt.colorClass(stats.avgDailyExcess), '按对齐交易日')}
      ${alphaMetric('相对净值', Fmt.pct(stats.relativeFinal), relativeClass, '策略净值 / 基准净值 - 1')}
    </div>
    <div class="mt-4 grid grid-cols-1 lg:grid-cols-2 gap-4 text-sm">
      <div class="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div class="text-xs text-gray-500 mb-2">解释</div>
        <div class="text-gray-300">${alphaExplanation(m, stats, bmName)}</div>
      </div>
      <div class="rounded-lg border border-gray-800 bg-gray-950/40 p-4">
        <div class="text-xs text-gray-500 mb-2">样本质量</div>
        <div class="text-gray-300">基于 ${stats.sampleDays} 个共同交易日计算；${stats.missingDays > 0 ? `有 ${stats.missingDays} 个策略交易日缺少可对齐基准值。` : '策略和基准日期对齐完整。'}</div>
      </div>
    </div>
  `;

  renderRelativeStrengthChart(stats.relativeSeries, bmName);
}

function alphaMetric(label, value, cls, sub) {
  return `
    <div class="rounded-lg border border-gray-800 bg-gray-950/40 px-3 py-3">
      <div class="text-[11px] text-gray-500 mb-1">${escapeHtml(label)}</div>
      <div class="text-lg font-semibold ${cls}">${escapeHtml(value)}</div>
      <div class="text-[11px] text-gray-600 mt-1">${escapeHtml(sub || '')}</div>
    </div>
  `;
}

function computeBenchmarkBreakdown(curve, benchCurve) {
  const benchByDate = {};
  benchCurve.dates.forEach((d, i) => { benchByDate[d] = finite(benchCurve.values[i]); });

  const commonDates = curve.dates.filter(d => benchByDate[d] > 0);
  const missingDays = curve.dates.length - commonDates.length;
  if (commonDates.length < 2) {
    return {
      sampleDays: 0,
      missingDays,
      winDays: 0,
      hitRate: 0,
      avgDailyExcess: 0,
      relativeFinal: 0,
      relativeSeries: [],
    };
  }

  const strategyByDate = {};
  curve.dates.forEach((d, i) => { strategyByDate[d] = finite(curve.values[i]); });
  let winDays = 0;
  let excessSum = 0;
  const relativeSeries = [];
  const startDate = commonDates[0];
  const stratStart = strategyByDate[startDate];
  const benchStart = benchByDate[startDate];

  for (let i = 1; i < commonDates.length; i += 1) {
    const prev = commonDates[i - 1];
    const cur = commonDates[i];
    const stratRet = strategyByDate[prev] > 0 ? strategyByDate[cur] / strategyByDate[prev] - 1 : 0;
    const benchRet = benchByDate[prev] > 0 ? benchByDate[cur] / benchByDate[prev] - 1 : 0;
    const excess = stratRet - benchRet;
    if (excess > 0) winDays += 1;
    excessSum += excess;
  }

  commonDates.forEach(d => {
    const stratNorm = stratStart > 0 ? strategyByDate[d] / stratStart : 1;
    const benchNorm = benchStart > 0 ? benchByDate[d] / benchStart : 1;
    relativeSeries.push({
      date: d,
      value: benchNorm > 0 ? stratNorm / benchNorm - 1 : 0,
    });
  });

  const sampleDays = commonDates.length - 1;
  const relativeFinal = relativeSeries.length ? relativeSeries[relativeSeries.length - 1].value : 0;
  return {
    sampleDays,
    missingDays,
    winDays,
    hitRate: sampleDays > 0 ? winDays / sampleDays : 0,
    avgDailyExcess: sampleDays > 0 ? excessSum / sampleDays : 0,
    relativeFinal,
    relativeSeries,
  };
}

function betaHint(beta) {
  const b = finite(beta);
  if (Math.abs(b) < 0.2) return '基准敏感度低';
  if (b < 0.8) return '低于基准波动';
  if (b <= 1.2) return '接近基准波动';
  return '高于基准波动';
}

function alphaExplanation(m, stats, bmName) {
  const parts = [];
  if (m.excess_return >= 0) {
    parts.push(`策略全周期跑赢 ${bmName} ${Fmt.pct(m.excess_return)}。`);
  } else {
    parts.push(`策略全周期跑输 ${bmName} ${pctPlain(Math.abs(m.excess_return))}。`);
  }
  if (m.alpha >= 0) {
    parts.push(`在 Beta=${Fmt.num(m.beta, 2)} 的基准暴露下，Jensen Alpha 为正。`);
  } else {
    parts.push(`在 Beta=${Fmt.num(m.beta, 2)} 的基准暴露下，Jensen Alpha 为负。`);
  }
  parts.push(`共同交易日内跑赢比例 ${pctPlain(stats.hitRate, 1)}。`);
  return parts.join('');
}

function pctPlain(v, digits = 2) {
  if (v == null) return '—';
  return (v * 100).toFixed(digits) + '%';
}

function renderRelativeStrengthChart(series, bmName) {
  const chartEl = document.getElementById('relative-strength-chart');
  if (!chartEl || !series.length) return;
  const chart = echarts.init(chartEl, null, { renderer: 'canvas' });
  chart.setOption({
    backgroundColor: 'transparent',
    grid: { left: 56, right: 20, top: 20, bottom: 36 },
    xAxis: {
      type: 'category',
      data: series.map(p => p.date),
      axisLabel: { color: '#6b7280', fontSize: 10 },
      axisLine: { lineStyle: { color: '#374151' } },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: '#6b7280', fontSize: 10, formatter: v => (v * 100).toFixed(1) + '%' },
      splitLine: { lineStyle: { color: '#1f2937' } },
    },
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#1f2937',
      borderColor: '#374151',
      textStyle: { color: '#e5e7eb', fontSize: 12 },
      formatter: p => `<b>${p[0].axisValue}</b><br/>相对 ${bmName}: ${Fmt.pct(p[0].data, 2)}`,
    },
    series: [{
      name: `相对 ${bmName}`,
      type: 'line',
      data: series.map(p => +p.value.toFixed(6)),
      symbol: 'none',
      lineStyle: { color: '#a78bfa', width: 1.5 },
      areaStyle: { color: 'rgba(167,139,250,0.12)' },
      markLine: {
        symbol: 'none',
        label: { show: false },
        lineStyle: { color: '#4b5563', type: 'dashed', width: 1 },
        data: [{ yAxis: 0 }],
      },
    }],
  });
  window.addEventListener('resize', () => chart.resize());
}

// ── 策略体检 ──────────────────────────────────────────────────────────────────
function renderStrategyAssessment(data) {
  const panel = document.getElementById('strategy-assessment');
  if (!panel) return;

  const assessment = assessBacktest(data.metrics, data.symbols || [], data.start_date, data.end_date);
  const notes = assessment.notes.length ? assessment.notes : ['核心指标未出现明显异常'];
  const actions = assessment.actions.length ? assessment.actions : ['进入更长周期和更大股票池复验'];

  panel.innerHTML = `
    <div class="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-5">
      <div>
        <h2 class="text-sm font-semibold text-gray-300 mb-3">策略体检</h2>
        <div class="flex items-end gap-3">
          <div class="text-4xl font-bold ${assessment.scoreClass}">${assessment.score}</div>
          <div class="pb-1">
            <div class="text-sm font-semibold ${assessment.scoreClass}">${assessment.rating}</div>
            <div class="text-xs text-gray-500">${assessment.sampleLabel}</div>
          </div>
        </div>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 lg:min-w-[560px]">
        ${assessment.badges.map(b => `
          <div class="bg-gray-800/70 border border-gray-700 rounded-lg px-3 py-2">
            <div class="text-[11px] text-gray-500 mb-1">${b.label}</div>
            <div class="text-sm font-semibold ${b.cls}">${b.value}</div>
          </div>
        `).join('')}
      </div>
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-5 mt-5">
      <div>
        <div class="text-xs text-gray-500 mb-2">主要判断</div>
        <div class="space-y-2">${notes.map(n => `<div class="text-sm text-gray-300">${n}</div>`).join('')}</div>
      </div>
      <div>
        <div class="text-xs text-gray-500 mb-2">下一步</div>
        <div class="space-y-2">${actions.map(n => `<div class="text-sm text-gray-300">${n}</div>`).join('')}</div>
      </div>
    </div>
  `;
}

function assessBacktest(m, symbols, startDate, endDate) {
  const annual = finite(m.annual_return);
  const sharpe = finite(m.sharpe_ratio);
  const calmar = finite(m.calmar_ratio);
  const drawdown = Math.abs(finite(m.max_drawdown));
  const trades = finite(m.total_trades);
  const years = sampleYears(startDate, endDate);
  const symbolCount = symbols.length;
  const initial = finite(m.initial_value) || 1;
  const fees = finite(m.total_fees);
  const feeDrag = fees / initial;

  let score = 50;
  score += clamp(annual * 220, -25, 25);
  score += clamp(sharpe * 8, -10, 14);
  score += clamp(calmar * 12, -12, 18);
  if (drawdown <= 0.08) score += 10;
  else if (drawdown <= 0.15) score += 4;
  else if (drawdown > 0.35) score -= 24;
  else if (drawdown > 0.25) score -= 15;
  else if (drawdown > 0.18) score -= 8;
  if (trades < 8) score -= 12;
  if (years < 2) score -= 10;
  if (symbolCount < 20) score -= 6;
  if (feeDrag > 0.03) score -= 6;
  score = Math.round(clamp(score, 0, 100));

  const notes = [];
  const actions = [];
  if (drawdown > 0.25) {
    notes.push(`最大回撤 ${Fmt.pct(-drawdown)}，资金曲线承压明显`);
    actions.push('优先检查仓位上限、趋势过滤和止损规则');
  } else if (drawdown <= 0.10) {
    notes.push(`最大回撤控制在 ${Fmt.pct(-drawdown)}，风险暴露较克制`);
  }
  if (calmar < 0.3) notes.push(`卡玛比率 ${Fmt.num(calmar, 2)}，单位回撤换来的收益偏弱`);
  if (sharpe < 0.5) notes.push(`夏普比率 ${Fmt.num(sharpe, 2)}，收益稳定性还不足`);
  if (trades < 8) {
    notes.push(`成交 ${trades} 笔，交易样本偏少`);
    actions.push('扩大股票池或延长区间后再判断参数是否有效');
  }
  if (symbolCount < 20) {
    notes.push(`股票池 ${symbolCount} 只，更像功能验证样本`);
    actions.push('正式比较建议覆盖 50 只以上并包含不同行业');
  }
  if (years < 3) {
    notes.push(`回测跨度 ${years.toFixed(1)} 年，市场阶段覆盖有限`);
    actions.push('补充牛市、熊市、震荡市三类区间复验');
  }
  if (feeDrag > 0.03) {
    notes.push(`手续费占初始资金 ${Fmt.pct(feeDrag)}，换手成本需要关注`);
    actions.push('检查调仓频率和单笔交易门槛');
  }

  const rating = score >= 75 ? '可进入样本外验证' : score >= 60 ? '有继续优化价值' : score >= 45 ? '仅适合研究观察' : '暂不适合实盘参考';
  const scoreClass = score >= 75 ? 'text-red-400' : score >= 60 ? 'text-yellow-300' : score >= 45 ? 'text-gray-300' : 'text-green-400';
  const sampleLabel = `${symbolCount} 只股票 / ${years.toFixed(1)} 年样本`;

  return {
    score,
    rating,
    scoreClass,
    sampleLabel,
    notes,
    actions,
    badges: [
      { label: '收益/回撤', value: ratioText(annual, drawdown), cls: calmar >= 0.8 ? 'text-red-400' : calmar >= 0.3 ? 'text-gray-200' : 'text-green-400' },
      { label: '最大回撤', value: Fmt.pct(-drawdown), cls: drawdown <= 0.15 ? 'text-red-400' : drawdown <= 0.25 ? 'text-yellow-300' : 'text-green-400' },
      { label: '交易样本', value: `${trades} 笔`, cls: trades >= 30 ? 'text-red-400' : trades >= 8 ? 'text-gray-200' : 'text-yellow-300' },
      { label: '费用拖累', value: Fmt.pct(feeDrag), cls: feeDrag <= 0.015 ? 'text-gray-200' : 'text-yellow-300' },
    ],
  };
}

function ratioText(annual, drawdown) {
  if (!drawdown) return '—';
  return `${Fmt.pct(annual)} / ${Fmt.pct(-drawdown)}`;
}

function sampleYears(startDate, endDate) {
  const start = Date.parse(startDate);
  const end = Date.parse(endDate);
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return 0;
  return (end - start) / 86400000 / 365.25;
}

function finite(v) {
  return Number.isFinite(Number(v)) ? Number(v) : 0;
}

function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v));
}

// ── 权益曲线图 ─────────────────────────────────────────────────────────────────
function renderEquityChart(curve, metrics, trades = [], benchCurve = null, benchLabel = null) {
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
          const rawValue = Array.isArray(p.value) ? p.value[1] : p.value;
          const val = p.seriesName === '回撤%'
            ? rawValue.toFixed(2) + '%'
            : Fmt.money(rawValue);
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
      // 基准线（如果有）
      ...(benchCurve && benchCurve.values && benchCurve.values.length > 0 ? [{
        name: benchLabel || '基准',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: benchCurve.dates
          ? benchCurve.dates.map((d, i) => [d, benchCurve.values[i]])
          : benchCurve.values,
        smooth: false,
        symbol: 'none',
        lineStyle: { color: '#fbbf24', width: 1.5, type: 'dashed' },
      }] : []),
    ],
  };

  chart.setOption(option);
  window.addEventListener('resize', () => chart.resize());
}

function benchmarkName(code) {
  const names = {
    '000300.SH': '沪深300',
    '000001.SH': '上证综指',
    '399006.SZ': '创业板指',
  };
  return names[code] || code || '基准';
}

// ── 详细指标 ──────────────────────────────────────────────────────────────────
function renderDetailMetrics(m, data = {}) {
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

  if (data.benchmark) {
    const bmName = data.benchmark_name || benchmarkName(data.benchmark);
    if (data.alpha_beta_available) {
      rows.push(
        ['基准', bmName, ''],
        ['基准收益', Fmt.pct(m.benchmark_return), Fmt.colorClass(m.benchmark_return)],
        ['超额收益', Fmt.pct(m.excess_return), Fmt.colorClass(m.excess_return)],
        ['Alpha', Fmt.num(m.alpha, 4), Fmt.colorClass(m.alpha)],
        ['Beta', Fmt.num(m.beta, 4), ''],
      );
    } else {
      rows.push(['基准对比', `${bmName}: ${data.benchmark_error || '未计算 Alpha/Beta'}`, 'text-yellow-400']);
    }
  }

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
    `卖出 ${trades.filter(t => t.side === 'SELL').length} 笔` +
    `${resultData?.rejected_count ? `，拒单 ${resultData.rejected_count} 次` : ''}`;

  // 股票筛选
  const filterInput = document.getElementById('trade-filter');
  if (filterInput) {
    filterInput.addEventListener('input', () => {
      const q = filterInput.value.trim().toLowerCase();
      const rows = tbody.querySelectorAll('tr');
      rows.forEach(row => {
        if (!q) { row.style.display = ''; return; }
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(q) ? '' : 'none';
      });
    });
  }
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
