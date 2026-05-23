from __future__ import annotations

import json

import pandas as pd
from click.testing import CliRunner

from cq.cli import main


def _price_frame() -> pd.DataFrame:
    rows = []
    dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
    closes = {
        "000001.SZ": [10, 11, 13, 14, 15],
        "000002.SZ": [10, 10.5, 12, 11, 10],
        "600000.SH": [10, 10, 10.5, 14, 16],
    }
    for symbol, values in closes.items():
        for trade_date, close in zip(dates, values, strict=True):
            rows.append({"date": trade_date, "symbol": symbol, "open": close, "close": close})
    return pd.DataFrame(rows)


def test_cli_help_smoke():
    runner = CliRunner()

    result = runner.invoke(main, ["--help"])
    factor = runner.invoke(main, ["factor-report", "--help"])
    cross = runner.invoke(main, ["cross-validate", "--help"])
    pit_validate = runner.invoke(main, ["validate-pit-universe", "--help"])
    pit_fetch = runner.invoke(main, ["fetch-pit-universe", "--help"])
    template = runner.invoke(main, ["cross-validation-template", "--help"])

    assert result.exit_code == 0
    assert "benchmark" in result.output
    assert factor.exit_code == 0
    assert cross.exit_code == 0
    assert pit_validate.exit_code == 0
    assert pit_fetch.exit_code == 0
    assert template.exit_code == 0


def test_cli_import_pit_universe_writes_standard_csv_and_diagnostics(tmp_path):
    source = tmp_path / "external.csv"
    source.write_text(
        "universe_id,symbol,start_date,end_date,name\n"
        "HS300_PIT,600519.sh,2020-01-01,,沪深300\n"
        "HS300_PIT,000001.sz,2020-01-01,2021-12-31,沪深300\n",
        encoding="utf-8",
    )
    output = tmp_path / "pit.csv"
    diagnostics = tmp_path / "pit.json"

    result = CliRunner().invoke(
        main,
        [
            "import-pit-universe",
            "--input",
            str(source),
            "--output",
            str(output),
            "--diagnostics",
            str(diagnostics),
        ],
    )

    assert result.exit_code == 0
    normalized = pd.read_csv(output)
    assert normalized["universe_id"].tolist() == ["hs300_pit", "hs300_pit"]
    assert normalized["symbol"].tolist() == ["000001.SZ", "600519.SH"]
    payload = json.loads(diagnostics.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "pit_membership.v1"
    assert payload["universes"]["hs300_pit"]["symbols"] == 2


def test_cli_benchmark_exports_reproducible_package_with_pit_filter(tmp_path):
    prices = tmp_path / "prices.csv"
    _price_frame().to_csv(prices, index=False)
    pit = tmp_path / "pit.csv"
    pit.write_text(
        "universe_id,symbol,start_date,end_date,name\n"
        "HS300_PIT,000001.SZ,2024-01-01,,沪深300\n"
        "HS300_PIT,600000.SH,2024-01-04,,沪深300\n",
        encoding="utf-8",
    )
    out = tmp_path / "benchmark"

    result = CliRunner().invoke(
        main,
        [
            "benchmark",
            "--price-csv",
            str(prices),
            "--output-dir",
            str(out),
            "--lookback",
            "1",
            "--top-n",
            "1",
            "--rebalance",
            "D",
            "--pit-csv",
            str(pit),
            "--universe-id",
            "HS300_PIT",
        ],
    )

    assert result.exit_code == 0
    assert (out / "config.json").exists()
    assert (out / "equity_curve.csv").exists()
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["config"]["universe_id"] == "HS300_PIT"
    assert summary["metadata"]["universe_diagnostics"]["min_members"] == 1


def test_cli_validate_pit_universe_exports_report_and_uses_exit_code(tmp_path):
    source = tmp_path / "pit.csv"
    source.write_text(
        "universe_id,symbol,start_date,end_date,name\n"
        "HS300_PIT,000001.SZ,2020-01-01,,沪深300\n"
        "HS300_PIT,600000.SH,2020-01-01,,沪深300\n",
        encoding="utf-8",
    )
    out = tmp_path / "validation"

    result = CliRunner().invoke(
        main,
        [
            "validate-pit-universe",
            "--input",
            str(source),
            "--output-dir",
            str(out),
            "--expected-universe",
            "HS300_PIT",
            "--min-symbols",
            "2",
            "--coverage-start",
            "2020-06-01",
            "--coverage-end",
            "2020-12-31",
        ],
    )

    assert result.exit_code == 0, result.output
    summary = json.loads((out / "pit_validation_summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True
    assert (out / "pit_validation_report.md").exists()


def test_cli_validate_pit_universe_fails_for_missing_expected_universe(tmp_path):
    source = tmp_path / "pit.csv"
    source.write_text(
        "universe_id,symbol,start_date,end_date,name\n"
        "HS300_PIT,000001.SZ,2020-01-01,,沪深300\n",
        encoding="utf-8",
    )
    out = tmp_path / "validation"

    result = CliRunner().invoke(
        main,
        [
            "validate-pit-universe",
            "--input",
            str(source),
            "--output-dir",
            str(out),
            "--expected-universe",
            "ZZ500_PIT",
        ],
    )

    assert result.exit_code == 1
    summary = json.loads((out / "pit_validation_summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] is False
    assert summary["error_count"] >= 1


def test_cli_cross_validation_template_exports_contract(tmp_path):
    out = tmp_path / "template"

    result = CliRunner().invoke(
        main,
        [
            "cross-validation-template",
            "--output-dir",
            str(out),
            "--platform-name",
            "QMT",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (out / "equity_curve.csv").exists()
    assert (out / "holdings.csv").exists()
    assert (out / "trades.csv").exists()
    assumptions = json.loads((out / "external_platform_assumptions.json").read_text(encoding="utf-8"))
    assert assumptions["platform_name"] == "QMT"


def test_cli_fetch_pit_universe_uses_tushare_source_and_exports_files(tmp_path, monkeypatch):
    import cq.universe.sources.tushare_pit as tushare_pit

    class FakeClient:
        def index_weight(self, **kwargs):
            trade_date = kwargs["start_date"]
            return pd.DataFrame(
                [
                    {
                        "index_code": kwargs["index_code"],
                        "con_code": "000001.SZ",
                        "trade_date": trade_date,
                        "weight": 1.0,
                    }
                ]
            )

    monkeypatch.setenv("TUSHARE_TOKEN", "test-token")
    monkeypatch.setattr(tushare_pit, "create_tushare_client", lambda token: FakeClient())
    out = tmp_path / "pit.csv"
    weights = tmp_path / "weights.csv"
    validation = tmp_path / "validation"

    result = CliRunner().invoke(
        main,
        [
            "fetch-pit-universe",
            "--provider",
            "tushare",
            "--start",
            "2024-01-01",
            "--end",
            "2024-01-31",
            "--index",
            "HS300_PIT=399300.SZ",
            "--output",
            str(out),
            "--weights-output",
            str(weights),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--validation-dir",
            str(validation),
            "--min-symbols",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert out.exists()
    assert weights.exists()
    assert (validation / "pit_validation_summary.json").exists()
    memberships = pd.read_csv(out)
    assert memberships[["universe_id", "symbol", "start_date"]].to_dict("records") == [
        {"universe_id": "hs300_pit", "symbol": "000001.SZ", "start_date": "2024-01-01"}
    ]


def test_cli_fetch_pit_universe_uses_akshare_source_and_exports_latest_snapshot(tmp_path, monkeypatch):
    import cq.universe.sources.akshare_pit as akshare_pit

    class FakeClient:
        def index_stock_cons_weight_csindex(self, symbol):
            return pd.DataFrame(
                [
                    {
                        "日期": "2024-05-31",
                        "指数代码": symbol,
                        "指数名称": "沪深300",
                        "成分券代码": "000001",
                        "成分券名称": "平安银行",
                        "交易所": "深圳证券交易所",
                        "权重": 1.0,
                    }
                ]
            )

        def index_stock_cons_csindex(self, symbol):
            raise AssertionError("weight endpoint should be used")

    monkeypatch.setattr(akshare_pit, "create_akshare_client", lambda: FakeClient())
    out = tmp_path / "pit.csv"
    weights = tmp_path / "weights.csv"
    validation = tmp_path / "validation"

    result = CliRunner().invoke(
        main,
        [
            "fetch-pit-universe",
            "--provider",
            "akshare",
            "--start",
            "2024-01-01",
            "--end",
            "2024-12-31",
            "--index",
            "HS300_PIT=000300",
            "--output",
            str(out),
            "--weights-output",
            str(weights),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--validation-dir",
            str(validation),
            "--min-symbols",
            "1",
        ],
    )

    assert result.exit_code == 0, result.output
    memberships = pd.read_csv(out)
    assert memberships[["universe_id", "symbol", "start_date"]].to_dict("records") == [
        {"universe_id": "hs300_pit", "symbol": "000001.SZ", "start_date": "2024-05-31"}
    ]
    summary = json.loads((validation / "pit_validation_summary.json").read_text(encoding="utf-8"))
    assert summary["passed"] is True
    fetch_summary = json.loads((validation / "pit_fetch_summary.json").read_text(encoding="utf-8"))
    assert fetch_summary["provider"] == "akshare"
    assert fetch_summary["effective_coverage_start"] == "2024-05-31"


def test_cli_fetch_akshare_pit_output_can_feed_benchmark_smoke(tmp_path, monkeypatch):
    import cq.universe.sources.akshare_pit as akshare_pit

    class FakeClient:
        def index_stock_cons_weight_csindex(self, symbol):
            return pd.DataFrame(
                [
                    {
                        "日期": "2024-05-31",
                        "指数代码": symbol,
                        "指数名称": "沪深300",
                        "成分券代码": "000001",
                        "成分券名称": "平安银行",
                        "交易所": "深圳证券交易所",
                        "权重": 1.0,
                    },
                    {
                        "日期": "2024-05-31",
                        "指数代码": symbol,
                        "指数名称": "沪深300",
                        "成分券代码": "600000",
                        "成分券名称": "浦发银行",
                        "交易所": "上海证券交易所",
                        "权重": 0.8,
                    },
                ]
            )

        def index_stock_cons_csindex(self, symbol):
            raise AssertionError("weight endpoint should be used")

    monkeypatch.setattr(akshare_pit, "create_akshare_client", lambda: FakeClient())
    pit_csv = tmp_path / "pit.csv"
    weights = tmp_path / "weights.csv"
    validation = tmp_path / "validation"
    fetch_result = CliRunner().invoke(
        main,
        [
            "fetch-pit-universe",
            "--provider",
            "akshare",
            "--start",
            "2024-01-01",
            "--end",
            "2024-12-31",
            "--index",
            "HS300_PIT=000300",
            "--output",
            str(pit_csv),
            "--weights-output",
            str(weights),
            "--raw-dir",
            str(tmp_path / "raw"),
            "--validation-dir",
            str(validation),
            "--min-symbols",
            "2",
        ],
    )
    assert fetch_result.exit_code == 0, fetch_result.output

    prices = tmp_path / "prices.csv"
    pd.DataFrame(
        [
            {"date": "2024-05-31", "symbol": "000001.SZ", "open": 10, "close": 10},
            {"date": "2024-05-31", "symbol": "600000.SH", "open": 10, "close": 10},
            {"date": "2024-05-31", "symbol": "000999.SZ", "open": 10, "close": 10},
            {"date": "2024-06-03", "symbol": "000001.SZ", "open": 11, "close": 12},
            {"date": "2024-06-03", "symbol": "600000.SH", "open": 10, "close": 10.5},
            {"date": "2024-06-03", "symbol": "000999.SZ", "open": 20, "close": 30},
            {"date": "2024-06-04", "symbol": "000001.SZ", "open": 12, "close": 12.5},
            {"date": "2024-06-04", "symbol": "600000.SH", "open": 10.5, "close": 10.8},
            {"date": "2024-06-04", "symbol": "000999.SZ", "open": 30, "close": 31},
        ]
    ).to_csv(prices, index=False)
    benchmark_out = tmp_path / "benchmark"
    benchmark_result = CliRunner().invoke(
        main,
        [
            "benchmark",
            "--price-csv",
            str(prices),
            "--output-dir",
            str(benchmark_out),
            "--lookback",
            "1",
            "--top-n",
            "1",
            "--rebalance",
            "D",
            "--pit-csv",
            str(pit_csv),
            "--universe-id",
            "HS300_PIT",
        ],
    )

    assert benchmark_result.exit_code == 0, benchmark_result.output
    summary = json.loads((benchmark_out / "summary.json").read_text(encoding="utf-8"))
    diagnostics = summary["metadata"]["universe_diagnostics"]
    assert diagnostics["input_rows"] == 9
    assert diagnostics["output_rows"] == 6
    assert diagnostics["min_members"] == 2
    assert summary["metadata"]["universe_source"]["provider"] == "akshare"
    assert summary["metadata"]["universe_source"]["strict_historical_pit"] is False
    assert "latest snapshot" in summary["metadata"]["universe_quality_warning"]
    trades = pd.read_csv(benchmark_out / "trades.csv")
    assert set(trades["symbol"]).issubset({"000001.SZ", "600000.SH"})
