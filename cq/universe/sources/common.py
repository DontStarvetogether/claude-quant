"""Shared helpers for PIT universe data-source adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def write_pit_fetch_artifacts(
    summary: Mapping[str, Any],
    *,
    validation_dir: str | Path,
    sidecar_summary_path: str | Path,
) -> dict[str, str]:
    """Write PIT fetch JSON summaries and a human-readable Markdown report."""

    validation_path = Path(validation_dir)
    summary_path = validation_path / "pit_fetch_summary.json"
    report_path = validation_path / "pit_fetch_report.md"
    sidecar_path = Path(sidecar_summary_path)

    payload = dict(summary)
    payload["summary_path"] = str(summary_path)
    payload["report_path"] = str(report_path)
    payload["sidecar_summary_path"] = str(sidecar_path)

    _write_json(payload, summary_path)
    _write_json(payload, sidecar_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(generate_pit_fetch_report(payload), encoding="utf-8")
    return {
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "sidecar_summary_path": str(sidecar_path),
    }


def generate_pit_fetch_report(summary: Mapping[str, Any]) -> str:
    """Render a Markdown report for a PIT universe fetch run."""

    provider = _value(summary.get("provider"))
    quality = _value(summary.get("source_quality"))
    strict_pit = "是" if summary.get("strict_historical_pit") is True else "否"
    validation = "PASS" if summary.get("validation_passed") else "FAIL"
    lines = [
        "# PIT 股票池下载报告",
        "",
        "## 总览",
        "",
        "| 项目 | 值 |",
        "|---|---|",
        f"| 数据源 | {provider} |",
        f"| 数据质量 | {quality} |",
        f"| 严格历史 PIT | {strict_pit} |",
        f"| 校验结果 | {validation} |",
        f"| 请求开始 | {_value(summary.get('requested_start') or summary.get('start'))} |",
        f"| 请求结束 | {_value(summary.get('requested_end') or summary.get('end'))} |",
        f"| 有效覆盖起点 | {_value(summary.get('effective_coverage_start') or summary.get('start'))} |",
        f"| 成分区间行数 | {_value(summary.get('membership_rows'))} |",
        f"| 权重快照行数 | {_value(summary.get('weight_rows'))} |",
        f"| 原始文件数 | {_value(summary.get('raw_file_count'))} |",
    ]
    source_warning = summary.get("source_warning")
    if source_warning:
        lines.append(f"| 数据源提示 | {_value(source_warning)} |")

    lines.extend(["", "## 股票池快照", ""])
    snapshot_dates = summary.get("snapshot_dates")
    endpoints = summary.get("endpoints")
    if isinstance(snapshot_dates, Mapping) and snapshot_dates:
        lines.extend(["| 股票池 | 快照日期 | 接口 |", "|---|---|---|"])
        for universe_id in sorted(snapshot_dates):
            endpoint = endpoints.get(universe_id, "") if isinstance(endpoints, Mapping) else ""
            lines.append(f"| {universe_id} | {_value(snapshot_dates[universe_id])} | {_value(endpoint)} |")
    else:
        lines.append("未记录逐股票池快照日期。")

    lines.extend(
        [
            "",
            "## 输出文件",
            "",
            "| 文件 | 路径 |",
            "|---|---|",
            f"| PIT 成分股 | {_value(summary.get('output_csv'))} |",
            f"| PIT 权重 | {_value(summary.get('weights_output'))} |",
            f"| 校验目录 | {_value(summary.get('validation_dir'))} |",
            f"| JSON 摘要 | {_value(summary.get('summary_path'))} |",
            f"| Markdown 报告 | {_value(summary.get('report_path'))} |",
            f"| CSV 同名 sidecar | {_value(summary.get('sidecar_summary_path'))} |",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(payload: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _value(value: Any) -> str:
    if value is None or value == "":
        return "未记录"
    return str(value)
