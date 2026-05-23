"""Research utilities for factor analysis."""

from cq.research.forward_return import calculate_forward_returns
from cq.research.grouping import FactorGroupAnalysis, analyze_factor_groups
from cq.research.ic import calculate_ic, summarize_ic
from cq.research.report import (
    FactorReport,
    FactorReportExport,
    export_factor_report,
    generate_factor_report,
    sample_split_diagnostics,
)

__all__ = [
    "FactorGroupAnalysis",
    "FactorReport",
    "FactorReportExport",
    "analyze_factor_groups",
    "calculate_forward_returns",
    "calculate_ic",
    "export_factor_report",
    "generate_factor_report",
    "sample_split_diagnostics",
    "summarize_ic",
]
