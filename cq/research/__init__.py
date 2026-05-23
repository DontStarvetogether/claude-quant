"""Research utilities for factor analysis."""

from cq.research.forward_return import calculate_forward_returns
from cq.research.grouping import FactorGroupAnalysis, analyze_factor_groups
from cq.research.ic import calculate_ic, summarize_ic

__all__ = [
    "FactorGroupAnalysis",
    "analyze_factor_groups",
    "calculate_forward_returns",
    "calculate_ic",
    "summarize_ic",
]
