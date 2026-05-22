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
  const stats = normalizeBenchmarkDiagnostics(
    data.benchmark_diagnostics,
    data.equity_curve,
    data.benchmark_curve,
  );
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
        <div class="text-gray-300">基于 ${stats.sampleDays} 个共同交易日计算${stats.commonStart && stats.commonEnd ? `（${stats.commonStart} → ${stats.commonEnd}）` : ''}；${stats.missingDays > 0 ? `有 ${stats.missingDays} 个策略交易日缺少可对齐基准值。` : '策略和基准日期对齐完整。'}</div>
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

function normalizeBenchmarkDiagnostics(apiStats, curve, benchCurve) {
  const fallback = computeBenchmarkBreakdown(curve, benchCurve);
  if (!apiStats) return fallback;
  return {
    ...fallback,
    sampleDays: finite(apiStats.sample_days),
    missingDays: finite(apiStats.missing_days),
    winDays: finite(apiStats.win_days),
    hitRate: finite(apiStats.hit_rate),
    avgDailyExcess: finite(apiStats.avg_daily_excess),
    relativeFinal: finite(apiStats.relative_return),
    commonStart: apiStats.common_start,
    commonEnd: apiStats.common_end,
    aligned: apiStats.aligned === true,
    relativeSeries: fallback.relativeSeries,
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
