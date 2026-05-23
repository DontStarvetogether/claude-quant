// ── 数据质量 ──────────────────────────────────────────────────────────────────
function renderDataDiagnostics(diagnostics) {
  const section = document.getElementById('data-diagnostics');
  const body = document.getElementById('data-diagnostics-body');
  const summaryEl = document.getElementById('data-diagnostics-summary');
  if (!section || !body || !summaryEl) return;

  if (!diagnostics) {
    section.classList.add('hidden');
    return;
  }

  const items = [
    ...(diagnostics.symbols || []),
    ...(diagnostics.benchmark ? [diagnostics.benchmark] : []),
  ];
  if (!items.length) {
    section.classList.add('hidden');
    return;
  }

  section.classList.remove('hidden');
  const summary = diagnostics.summary || {};
  summaryEl.textContent = `更新 ${summary.updated || 0} / 缓存 ${summary.cache_hit || 0} / 失败 ${summary.failed || 0} / 缺失 ${summary.missing || 0}`;

  body.innerHTML = `
    <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
      ${items.map(renderDataDiagnosticCard).join('')}
    </div>
  `;
}

// ── 股票池诊断 ────────────────────────────────────────────────────────────────
function renderUniverseDiagnostics(diagnostics) {
  const section = document.getElementById('universe-diagnostics');
  const body = document.getElementById('universe-diagnostics-body');
  const summaryEl = document.getElementById('universe-diagnostics-summary');
  if (!section || !body || !summaryEl) return;

  if (!diagnostics) {
    section.classList.add('hidden');
    return;
  }

  section.classList.remove('hidden');
  const risk = universeRiskMeta(diagnostics.survivorship_bias_risk);
  const pointInTime = diagnostics.point_in_time ? '是' : '否';
  const historyMembership = diagnostics.history_membership_available ? '可用' : '不可用';
  summaryEl.textContent = `${diagnostics.symbol_count || 0} 只 · ${risk.label}`;

  body.innerHTML = `
    <div class="rounded-lg border ${risk.border} bg-gray-950/40 px-4 py-4">
      <div class="flex items-start justify-between gap-4 mb-3">
        <div>
          <div class="text-sm font-medium text-gray-200">${escapeHtml(diagnostics.universe_name || '自定义静态股票池')}</div>
          <div class="text-xs text-gray-600 mt-1">${escapeHtml(diagnostics.universe_id || 'custom_static')} · ${escapeHtml(diagnostics.source || 'user_selection')}</div>
        </div>
        <span class="text-xs px-2 py-0.5 rounded border ${risk.badge}">${escapeHtml(risk.label)}</span>
      </div>
      <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
        <div><div class="text-gray-600">构造方式</div><div class="text-gray-300">${escapeHtml(universeConstructionLabel(diagnostics.construction))}</div></div>
        <div><div class="text-gray-600">成分时间点</div><div class="text-gray-300">${escapeHtml(diagnostics.selection_time || 'run_submit')}</div></div>
        <div><div class="text-gray-600">历史成分</div><div class="text-gray-300">${historyMembership}</div></div>
        <div><div class="text-gray-600">Point-in-time</div><div class="text-gray-300">${pointInTime}</div></div>
      </div>
      ${renderUniverseMessages(diagnostics)}
    </div>
  `;
}

function renderUniverseMessages(diagnostics) {
  const messages = [
    ...(diagnostics.warnings || []).map(w => universeWarningText(w)),
    ...(diagnostics.notes || []),
  ].filter(Boolean);
  if (!messages.length) return '';
  return `
    <div class="space-y-1 text-xs text-yellow-300">
      ${messages.map(msg => `<div>${escapeHtml(msg)}</div>`).join('')}
    </div>
  `;
}

function universeRiskMeta(risk) {
  const map = {
    high: {
      label: '幸存者偏差高',
      border: 'border-red-800/70',
      badge: 'border-red-700 bg-red-950/40 text-red-300',
    },
    medium: {
      label: '幸存者偏差中',
      border: 'border-yellow-800/70',
      badge: 'border-yellow-700 bg-yellow-950/40 text-yellow-300',
    },
    low: {
      label: '幸存者偏差低',
      border: 'border-gray-800',
      badge: 'border-gray-700 bg-gray-900 text-gray-300',
    },
  };
  return map[risk] || {
    label: '偏差未知',
    border: 'border-gray-800',
    badge: 'border-gray-700 bg-gray-900 text-gray-300',
  };
}

function universeConstructionLabel(value) {
  const map = {
    static: '静态股票池',
    point_in_time: '历史时点股票池',
  };
  return map[value] || value || '未知';
}

function universeWarningText(value) {
  const map = {
    static_universe_survivorship_bias: '当前股票池按提交时静态成分回测历史，结果可能包含幸存者偏差。',
  };
  return map[value] || value;
}

function renderDataDiagnosticCard(item) {
  const meta = dataStatusMeta(item.status);
  const role = item.role === 'benchmark' ? '基准' : '交易标的';
  const dates = item.local_first_date && item.local_last_date
    ? `${item.local_first_date} → ${item.local_last_date}`
    : '无本地缓存';
  const requested = item.requested_start && item.requested_end
    ? `${item.requested_start} → ${item.requested_end}`
    : '—';
  const quality = item.data_quality || {};
  const qualityLevel = item.quality_level || quality.quality_level || quality.status || 'unknown';
  const qualityText = quality.status
    ? `${qualityStatusLabel(qualityLevel)}${(quality.warnings || []).length ? ` · ${(quality.warnings || []).join(', ')}` : ''}`
    : '—';
  const provenance = [
    item.source ? `源 ${item.source}` : null,
    item.cache_updated_at ? `缓存 ${item.cache_updated_at.slice(0, 19).replace('T', ' ')}` : null,
  ].filter(Boolean).join(' · ');
  const qfqRange = item.qfq_first_date && item.qfq_last_date
    ? `${item.qfq_first_date} → ${item.qfq_last_date}`
    : '无 qfq';
  const factorRange = item.factor_first_date && item.factor_last_date
    ? `${item.factor_first_date} → ${item.factor_last_date}`
    : (item.factor_available ? '可用' : '无因子');
  return `
    <div class="rounded-lg border ${meta.border} bg-gray-950/40 px-3 py-3">
      <div class="flex items-start justify-between gap-3 mb-2">
        <div>
          <div class="font-mono text-sm text-gray-200">${escapeHtml(item.symbol)}</div>
          <div class="text-[11px] text-gray-600">${escapeHtml(role)}</div>
        </div>
        <span class="text-[11px] px-2 py-0.5 rounded border ${meta.badge}">${escapeHtml(meta.label)}</span>
      </div>
      <div class="space-y-1 text-xs">
        <div class="flex justify-between gap-3"><span class="text-gray-600">新增</span><span class="text-gray-300">${Number(item.new_records || 0).toLocaleString()} 条</span></div>
        <div class="flex justify-between gap-3"><span class="text-gray-600">本地区间</span><span class="text-gray-400 text-right">${escapeHtml(dates)}</span></div>
        <div class="flex justify-between gap-3"><span class="text-gray-600">请求区间</span><span class="text-gray-500 text-right">${escapeHtml(requested)}</span></div>
        <div class="flex justify-between gap-3"><span class="text-gray-600">质量</span><span class="text-gray-400 text-right">${escapeHtml(qualityText)}</span></div>
        <div class="flex justify-between gap-3"><span class="text-gray-600">qfq 区间</span><span class="text-gray-500 text-right">${escapeHtml(qfqRange)}</span></div>
        <div class="flex justify-between gap-3"><span class="text-gray-600">复权因子</span><span class="text-gray-500 text-right">${escapeHtml(factorRange)}</span></div>
        <div class="flex justify-between gap-3"><span class="text-gray-600">ST 状态</span><span class="text-gray-500 text-right">${escapeHtml(stStatusLabel(item.st_status_source || quality.st_status_source))}</span></div>
        <div class="flex justify-between gap-3"><span class="text-gray-600">涨跌停价</span><span class="text-gray-500 text-right">${escapeHtml(limitPriceSourceLabel(item.limit_price_source || quality.limit_price_source))}</span></div>
        ${provenance ? `<div class="pt-1 text-[11px] text-gray-600">${escapeHtml(provenance)}</div>` : ''}
        ${(item.repair_actions || []).length ? `<div class="pt-1 text-[11px] text-blue-300">修复: ${escapeHtml(item.repair_actions.join(', '))}</div>` : ''}
        ${item.error ? `<div class="pt-1 text-yellow-300">${escapeHtml(item.error)}</div>` : ''}
      </div>
    </div>
  `;
}

function dataStatusMeta(status) {
  const map = {
    updated: {
      label: '已更新',
      border: 'border-green-800/70',
      badge: 'border-green-700 bg-green-950/40 text-green-300',
    },
    cache_hit: {
      label: '使用缓存',
      border: 'border-gray-800',
      badge: 'border-gray-700 bg-gray-900 text-gray-300',
    },
    download_failed_cache_available: {
      label: '失败但有缓存',
      border: 'border-yellow-800/70',
      badge: 'border-yellow-700 bg-yellow-950/40 text-yellow-300',
    },
    download_failed_no_cache: {
      label: '无可用缓存',
      border: 'border-red-800/70',
      badge: 'border-red-700 bg-red-950/40 text-red-300',
    },
    empty_source: {
      label: '源无数据',
      border: 'border-red-800/70',
      badge: 'border-red-700 bg-red-950/40 text-red-300',
    },
  };
  return map[status] || {
    label: status || '未知',
    border: 'border-gray-800',
    badge: 'border-gray-700 bg-gray-900 text-gray-300',
  };
}

function qualityStatusLabel(status) {
  const map = {
    pass: '通过',
    warning: '警告',
    failed: '失败',
    ok: '完整',
    degraded: '需注意',
    missing: '缺失',
    unknown: '未知',
  };
  return map[status] || status || '未知';
}

function stStatusLabel(value) {
  const map = {
    unavailable: '不可用',
    approximate: '近似',
    source: '数据源',
  };
  return map[value] || value || '未知';
}

function limitPriceSourceLabel(value) {
  const map = {
    exchange_or_calculated: '交易所/规则计算',
    approximate: '近似计算',
    unavailable: '不可用',
    unknown: '未知',
  };
  return map[value] || value || '未知';
}

function renderTrustOverview(data) {
  const section = document.getElementById('trust-overview');
  const body = document.getElementById('trust-overview-body');
  const summary = document.getElementById('trust-overview-summary');
  if (!section || !body || !summary) return;

  const dataQuality = data.data_quality || {};
  const exec = data.execution_diagnostics || {};
  const metric = data.metric_diagnostics || {};
  const universe = data.universe_diagnostics || {};
  const assumptions = data.execution_assumptions || {};
  const benchmarkSelected = data.benchmark != null && data.benchmark !== '';
  const cards = [
    trustCard('回测状态', 'completed', '已完成', 'pass'),
    trustCard('数据质量', dataQuality.status || 'unknown', dataQualityText(dataQuality), dataQuality.status || 'unknown'),
    trustCard('基准对比', benchmarkSelected ? data.benchmark_status : 'not_requested', benchmarkTrustText(data), benchmarkSelected ? (data.alpha_beta_available ? 'pass' : 'warning') : 'unknown'),
    trustCard('股票池偏差', universe.survivorship_bias_risk || 'unknown', universeRiskText(universe), universe.survivorship_bias_risk === 'high' ? 'failed' : universe.survivorship_bias_risk === 'medium' ? 'warning' : 'pass'),
    trustCard('撮合限制', exec.rejected_count || exec.capacity_limited_count ? 'warning' : 'pass', executionTrustText(exec), exec.rejected_count || exec.capacity_limited_count ? 'warning' : 'pass'),
    trustCard('指标样本', metric.quality_level || 'unknown', metricTrustText(metric), metric.quality_level || 'unknown'),
  ];

  summary.textContent = `${data.engine_version || 'legacy'} · ${data.execution_model || assumptions.execution_model || 'next_open'}`;
  body.innerHTML = `
    <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
      ${cards.join('')}
    </div>
    <div class="mt-4 rounded-lg border border-gray-800 bg-gray-950/40 px-4 py-3 text-xs text-gray-400">
      <div class="font-medium text-gray-300 mb-2">成交假设</div>
      <div class="grid grid-cols-1 md:grid-cols-3 gap-2">
        <div>${escapeHtml(assumptions.signal_timing || 'D 日收盘后产生信号')}</div>
        <div>${escapeHtml(assumptions.fill_timing || 'D+1 开盘成交')}</div>
        <div>${escapeHtml(assumptions.limit_order_semantics || '限价单按开盘价判断')}</div>
      </div>
    </div>
  `;
}

function trustCard(label, raw, text, level) {
  const meta = trustLevelMeta(level);
  return `
    <div class="rounded-lg border ${meta.border} bg-gray-950/40 px-4 py-3">
      <div class="flex items-center justify-between gap-3 mb-2">
        <div class="text-xs text-gray-500">${escapeHtml(label)}</div>
        <span class="text-[11px] px-2 py-0.5 rounded border ${meta.badge}">${escapeHtml(meta.label)}</span>
      </div>
      <div class="text-sm text-gray-200">${escapeHtml(text || raw || '—')}</div>
    </div>
  `;
}

function trustLevelMeta(level) {
  if (['pass', 'ok', 'available', 'completed', 'low'].includes(level)) {
    return { label: '通过', border: 'border-green-800/60', badge: 'border-green-700 bg-green-950/40 text-green-300' };
  }
  if (['warning', 'degraded', 'medium', 'unavailable'].includes(level)) {
    return { label: '需注意', border: 'border-yellow-800/70', badge: 'border-yellow-700 bg-yellow-950/40 text-yellow-300' };
  }
  if (['failed', 'missing', 'high'].includes(level)) {
    return { label: '高风险', border: 'border-red-800/70', badge: 'border-red-700 bg-red-950/40 text-red-300' };
  }
  return { label: '未知', border: 'border-gray-800', badge: 'border-gray-700 bg-gray-900 text-gray-300' };
}

function dataQualityText(q) {
  if (!q) return '历史结果缺少数据质量诊断';
  return `数据项 ${q.total ?? 0} 个，失败 ${q.failed ?? 0}，缺失 ${q.missing ?? 0}`;
}

function benchmarkTrustText(data) {
  if (!data.benchmark) return '未选择基准';
  if (data.alpha_beta_available) return `${data.benchmark_name || data.benchmark} 可计算 Alpha/Beta`;
  return data.benchmark_error || `${data.benchmark_name || data.benchmark} 不可计算 Alpha/Beta`;
}

function universeRiskText(universe) {
  if (!universe) return '历史结果缺少股票池诊断';
  return `${universe.symbol_count || 0} 只 · ${universeRiskMeta(universe.survivorship_bias_risk).label}`;
}

function executionTrustText(exec) {
  const rejected = exec.rejected_count || 0;
  const limited = exec.capacity_limited_count || 0;
  if (!rejected && !limited) return '无拒单或容量缩量';
  const cash = exec.rejected_by_cash || 0;
  const t1 = exec.rejected_by_t1 || 0;
  if (!cash && !t1) return `拒单 ${rejected}，容量缩量 ${limited}`;
  return `拒单 ${rejected}，容量缩量 ${limited}，现金 ${cash}，T+1 ${t1}`;
}

function metricTrustText(metric) {
  if (!metric || !Object.keys(metric).length) return '历史结果缺少指标诊断';
  return `${metric.sample_days || 0} 个收益样本，${metric.round_trip_count || 0} 个完整交易轮次`;
}

function renderDiagnosticsDetail(data) {
  const body = document.getElementById('diagnostics-detail-body');
  if (!body) return;
  const assumptions = data.execution_assumptions || {};
  const exec = data.execution_diagnostics || {};
  const metric = data.metric_diagnostics || {};
  const riskEvents = data.risk_events || [];
  const assumptionRows = [
    ['信号时点', assumptions.signal_timing || 'D 日收盘后产生信号'],
    ['成交时点', assumptions.fill_timing || 'D+1 开盘成交'],
    ['限价语义', assumptions.limit_order_semantics || '仅按开盘价判断'],
    ['日内触达', assumptions.uses_intraday_touch ? '使用 high/low' : '未使用 high/low'],
    ['容量限制', assumptions.capacity_limit_enabled ? `启用，最大参与率 ${Fmt.pct(assumptions.max_volume_participation || 0, 1)}` : '未启用'],
  ];
  const execRows = [
    ['订单总数', `${exec.order_count ?? ((exec.filled_count || 0) + (exec.rejected_count || 0))}`],
    ['成交订单率', Fmt.pct(exec.filled_order_rate ?? 1, 1)],
    ['容量缩量', `${exec.capacity_limited_count || 0}`],
    ['容量拒单', `${exec.capacity_rejected_count || 0}`],
    ['部分成交', `${exec.partial_fill_count || 0}`],
    ['部分成交比例', Fmt.pct(exec.partial_fill_ratio ?? exec.avg_fill_ratio ?? 1, 1)],
    ['涨停拒单', exec.rejected_by_limit_up != null ? `${exec.rejected_by_limit_up}` : '—'],
    ['跌停拒单', exec.rejected_by_limit_down != null ? `${exec.rejected_by_limit_down}` : '—'],
    ['现金不足', exec.rejected_by_cash != null ? `${exec.rejected_by_cash}` : '—'],
    ['T+1 限制', exec.rejected_by_t1 != null ? `${exec.rejected_by_t1}` : '—'],
    ['持仓不足', exec.rejected_by_position != null ? `${exec.rejected_by_position}` : '—'],
    ['停牌拒单', exec.rejected_by_suspended != null ? `${exec.rejected_by_suspended}` : '—'],
    ['限价拒单', exec.rejected_by_limit_order != null ? `${exec.rejected_by_limit_order}` : '—'],
  ];
  const metricWarnings = (metric.warnings || []).map(metricWarningText).join(' / ') || '无';
  const topReasons = (exec.top_reject_reasons || [])
    .map(item => `${item.reason} (${item.count})`)
    .join('；') || '无';
  body.innerHTML = `
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <div class="rounded-lg border border-gray-800 bg-gray-950/40 px-4 py-3">
        <div class="text-sm font-medium text-gray-300 mb-3">Execution</div>
        <div class="space-y-1 text-xs">
          ${assumptionRows.map(([k, v]) => `<div class="flex justify-between gap-3"><span class="text-gray-600">${escapeHtml(k)}</span><span class="text-gray-300 text-right">${escapeHtml(v)}</span></div>`).join('')}
          <div class="border-t border-gray-800 my-2"></div>
          ${execRows.map(([k, v]) => `<div class="flex justify-between gap-3"><span class="text-gray-600">${escapeHtml(k)}</span><span class="text-gray-300 text-right">${escapeHtml(v)}</span></div>`).join('')}
          <div class="flex justify-between gap-3"><span class="text-gray-600">主要拒单</span><span class="text-gray-300 text-right">${escapeHtml(topReasons)}</span></div>
        </div>
      </div>
      <div class="rounded-lg border border-gray-800 bg-gray-950/40 px-4 py-3">
        <div class="text-sm font-medium text-gray-300 mb-3">Metrics</div>
        <div class="space-y-1 text-xs">
          <div class="flex justify-between gap-3"><span class="text-gray-600">样本天数</span><span class="text-gray-300">${metric.sample_days ?? '—'}</span></div>
          <div class="flex justify-between gap-3"><span class="text-gray-600">胜率口径</span><span class="text-gray-300 text-right">${escapeHtml(metric.win_rate_basis || 'completed_round_trips_fifo')}</span></div>
          <div class="flex justify-between gap-3"><span class="text-gray-600">收益口径</span><span class="text-gray-300 text-right">${escapeHtml(metric.return_basis || 'equity_curve_eod')}</span></div>
          <div class="flex justify-between gap-3"><span class="text-gray-600">年化天数</span><span class="text-gray-300">${metric.annualization_trading_days || 252}</span></div>
          <div class="flex justify-between gap-3"><span class="text-gray-600">年化换手</span><span class="text-gray-300">${Fmt.pct(metric.annual_turnover || 0, 1)}</span></div>
          <div class="flex justify-between gap-3"><span class="text-gray-600">成本拖累</span><span class="text-gray-300">${Fmt.pct(metric.cost_drag || 0, 2)}</span></div>
          <div class="pt-1 text-yellow-300">${escapeHtml(metricWarnings)}</div>
        </div>
      </div>
      <div class="rounded-lg border border-gray-800 bg-gray-950/40 px-4 py-3">
        <div class="text-sm font-medium text-gray-300 mb-3">Risk Events</div>
        <div class="space-y-1 text-xs text-gray-400">
          ${riskEvents.length ? riskEvents.slice(0, 6).map(e => `<div>${escapeHtml(e.date || e.trade_date || '')} ${escapeHtml(e.type || e.event || '')} ${escapeHtml(e.reason || e.message || '')}</div>`).join('') : '<div class="text-gray-600">无风控事件</div>'}
        </div>
      </div>
    </div>
  `;
}

function metricWarningText(value) {
  const map = {
    sample_days_too_few: '样本天数偏少',
    round_trips_too_few: '完整交易轮次偏少',
    benchmark_unavailable: '基准不可用',
    annual_turnover_high: '年化换手偏高',
    cost_drag_high: '成本拖累偏高',
    data_quality_failed: '数据质量失败',
    data_quality_warning: '数据质量警告',
    orders_rejected: '存在拒单',
  };
  return map[value] || value;
}
