/**
 * API 调用封装
 */

const API = {
  async getStrategies() {
    const res = await fetch('/api/strategies');
    if (!res.ok) throw new Error('获取策略列表失败');
    return res.json();
  },

  async getUniverses() {
    const res = await fetch('/api/universes');
    if (!res.ok) throw new Error('获取股票池预设失败');
    return res.json();
  },

  async runBacktest(payload) {
    const res = await fetch('/api/backtest/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '提交回测失败');
    }
    return res.json();
  },

  async getStatus(runId) {
    const res = await fetch(`/api/backtest/${runId}/status`);
    if (!res.ok) throw new Error('获取状态失败');
    return res.json();
  },

  async getResult(runId) {
    const res = await fetch(`/api/backtest/${runId}/result`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || '获取结果失败');
    }
    return res.json();
  },

  async getHistory() {
    const res = await fetch('/api/backtest/history/list');
    if (!res.ok) return { runs: [] };
    return res.json();
  },

  async deleteRun(runId) {
    await fetch(`/api/backtest/${runId}`, { method: 'DELETE' });
  },

  async getSymbols(syncNames = false) {
    const url = syncNames ? '/api/symbols?sync_names=true' : '/api/symbols';
    const res = await fetch(url);
    if (!res.ok) throw new Error('获取股票池失败');
    return res.json();
  },
};

/**
 * 数字格式化工具
 */
const Fmt = {
  pct(v, digits = 2) {
    if (v == null) return '—';
    const s = (v * 100).toFixed(digits) + '%';
    return v >= 0 ? '+' + s : s;
  },

  num(v, digits = 4) {
    if (v == null) return '—';
    return v.toFixed(digits);
  },

  money(v) {
    if (v == null) return '—';
    return (v / 10000).toFixed(2) + ' 万';
  },

  colorClass(v) {
    if (v == null) return '';
    return v >= 0 ? 'positive' : 'negative';
  },
};
