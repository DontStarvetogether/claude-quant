(() => {
  const params = new URLSearchParams(window.location.search);
  for (const id of ["factor-id", "universe-id", "top-n", "rebalance"]) {
    const value = params.get(id.replaceAll("-", "_"));
    const el = document.getElementById(id);
    if (el && value) el.value = value;
  }

  const $ = (id) => document.getElementById(id);

  function setStatus(status, progress, message) {
    const pill = $("status-pill");
    const bar = $("progress-bar");
    const output = $("benchmark-output");
    if (pill) pill.textContent = status;
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, progress || 0))}%`;
    if (output && message) output.textContent = message;
  }

  async function fetchJson(url, options = {}) {
    const resp = await fetch(url, options);
    if (!resp.ok) {
      let message = `${resp.status} ${resp.statusText}`;
      try {
        const payload = await resp.json();
        message = payload.detail || payload.message || message;
      } catch {}
      throw new Error(message);
    }
    return resp.json();
  }

  function requestPayload() {
    return {
      price_csv: $("price-csv")?.value,
      output_dir: $("output-dir")?.value || null,
      universe_id: $("universe-id")?.value || null,
      lookback: 20,
      top_n: Number.parseInt($("top-n")?.value || "20", 10),
      rebalance: $("rebalance")?.value || "weekly",
    };
  }

  function watchRun(runId) {
    const source = new EventSource(`/api/benchmark/${runId}/stream`);
    source.addEventListener("progress", (event) => {
      const data = JSON.parse(event.data);
      setStatus("running", data.progress, data.message || data.current_step);
    });
    source.addEventListener("completed", async () => {
      source.close();
      const result = await fetchJson(`/api/benchmark/${runId}/result`);
      const summary = result.summary?.summary || {};
      const artifactLinks = Object.entries(result.artifacts || {})
        .map(([name, url]) => `<a href="${url}" class="text-blue-400 hover:text-blue-300">${name}</a>`)
        .join(" · ");
      $("benchmark-output").innerHTML = `
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
          <div><div class="text-xs text-gray-500">总收益</div><div class="text-gray-100">${fmtPct(summary.total_return)}</div></div>
          <div><div class="text-xs text-gray-500">年化收益</div><div class="text-gray-100">${fmtPct(summary.annual_return)}</div></div>
          <div><div class="text-xs text-gray-500">最大回撤</div><div class="text-gray-100">${fmtPct(summary.max_drawdown)}</div></div>
          <div><div class="text-xs text-gray-500">交易次数</div><div class="text-gray-100">${summary.trade_count ?? "—"}</div></div>
        </div>
        <div class="text-xs text-gray-500">导出文件：${artifactLinks}</div>
      `;
      setStatus("completed", 100);
    });
    source.addEventListener("error", (event) => {
      source.close();
      let message = "benchmark 失败";
      if (event.data) {
        try {
          message = JSON.parse(event.data).message || message;
        } catch {}
      }
      setStatus("failed", 100, message);
      const btn = $("submit-btn");
      if (btn) btn.disabled = false;
    });
  }

  function fmtPct(value) {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(2)}%` : "—";
  }

  $("benchmark-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const btn = $("submit-btn");
    if (btn) btn.disabled = true;
    setStatus("pending", 5, "提交 benchmark 任务");
    try {
      const payload = await fetchJson("/api/benchmark/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestPayload()),
      });
      setStatus(payload.status || "pending", 10, `任务 ${payload.run_id} 已创建`);
      watchRun(payload.run_id);
    } catch (err) {
      setStatus("failed", 100, err.message || String(err));
      if (btn) btn.disabled = false;
    }
  });
})();
