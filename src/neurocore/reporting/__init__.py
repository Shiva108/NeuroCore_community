"""Reporting utilities built on top of NeuroCore query outputs."""

from neurocore.reporting.consensus import (
    ConsensusReport,
    ExternalReportModelClient,
    MultiModelConsensusReporter,
)
from neurocore.reporting.workflows import (
    build_report_context_from_query_response,
    build_sectioned_report_prompt,
)

__all__ = [
    "ConsensusReport",
    "ExternalReportModelClient",
    "MultiModelConsensusReporter",
    "build_report_context_from_query_response",
    "build_sectioned_report_prompt",
]
