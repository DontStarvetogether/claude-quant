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
  const qualityText = quality.status
    ? `${qualityStatusLabel(quality.status)}${(quality.warnings || []).length ? ` · ${(quality.warnings || []).join(', ')}` : ''}`
    : '—';
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
    ok: '完整',
    degraded: '需注意',
    missing: '缺失',
    unknown: '未知',
  };
  return map[status] || status || '未知';
}
