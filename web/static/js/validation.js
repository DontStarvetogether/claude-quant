(() => {
  const $ = (id) => document.getElementById(id);
  const output = document.getElementById("validation-output");
  if (output) {
    output.textContent = "等待生成模板或运行对账。";
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

  function artifactLinks(payload) {
    return Object.entries(payload.artifacts || {})
      .map(([name, url]) => `<a href="${url}" class="text-blue-400 hover:text-blue-300">${name}</a>`)
      .join(" · ");
  }

  function showArtifacts(title, payload) {
    $("validation-output").innerHTML = `
      <div class="text-gray-300 mb-2">${title}</div>
      <div class="text-xs text-gray-500 mb-3">artifact_set_id: ${payload.artifact_set_id}</div>
      <div class="text-sm">${artifactLinks(payload)}</div>
    `;
  }

  $("template-btn")?.addEventListener("click", async () => {
    try {
      const payload = await fetchJson("/api/validation/template", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform_name: $("platform")?.value || "external",
          output_dir: $("output-dir")?.value || null,
        }),
      });
      showArtifacts("模板已生成", payload);
    } catch (err) {
      $("validation-output").textContent = err.message || String(err);
    }
  });

  $("validation-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    const btn = $("submit-btn");
    if (btn) btn.disabled = true;
    try {
      const payload = await fetchJson("/api/validation/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          platform_name: $("platform")?.value || "external",
          local_equity_csv: $("local-equity-csv")?.value,
          local_trades_csv: $("local-trades-csv")?.value || null,
          external_equity_csv: $("equity-csv")?.value,
          external_holdings_csv: $("holdings-csv")?.value || null,
          external_trades_csv: $("trades-csv")?.value || null,
          output_dir: $("output-dir")?.value || null,
        }),
      });
      const passed = payload.summary?.passed ? "PASS" : "FAIL";
      showArtifacts(`对账完成：${passed}`, payload);
    } catch (err) {
      $("validation-output").textContent = err.message || String(err);
    } finally {
      if (btn) btn.disabled = false;
    }
  });
})();
