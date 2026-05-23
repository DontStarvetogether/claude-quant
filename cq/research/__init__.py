"""Research utilities for factor analysis."""

from cq.research.forward_return import calculate_forward_returns
from cq.research.grouping import FactorGroupAnalysis, analyze_factor_groups
from cq.research.ic import calculate_ic, summarize_ic
from cq.research.report import FactorReport, generate_factor_report

__all__ = [
    "FactorGroupAnalysis",
    "FactorReport",
    "analyze_factor_groups",
    "calculate_forward_returns",
    "calculate_ic",
    "generate_factor_report",
    "summarize_ic",
]
