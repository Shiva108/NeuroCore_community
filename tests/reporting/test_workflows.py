from neurocore.reporting.workflows import (
    build_report_context_from_query_response,
    build_sectioned_report_prompt,
)


def test_build_report_context_from_query_response_formats_results():
    query_response = {
        "results": [
            {
                "id": "rec-1",
                "kind": "record",
                "content": "Potential secret found in runbook.",
                "score": 120.3,
                "source_type": "slack_message",
                "metadata": {"channel_id": "C123"},
            },
            {
                "id": "rec-2",
                "kind": "record",
                "content": "Credential rotation completed.",
                "score": 95.0,
                "source_type": "discord_message",
                "metadata": {"channel_id": "c-42"},
            },
        ]
    }

    context = build_report_context_from_query_response(query_response, max_items=1)

    assert "rec-1" in context
    assert "Potential secret found in runbook." in context
    assert "rec-2" not in context


def test_build_sectioned_report_prompt_contains_requested_sections():
    prompt = build_sectioned_report_prompt(
        objective="Generate a memory security review.",
        context_markdown="Captured context here",
        sections=("Overview", "Findings", "Risks", "Actions"),
    )

    assert "Generate a memory security review." in prompt
    assert "Captured context here" in prompt
    assert "## Overview" in prompt
    assert "## Findings" in prompt
    assert "## Risks" in prompt
    assert "## Actions" in prompt


def test_build_report_context_requires_positive_max_items():
    query_response = {
        "results": [
            {
                "id": "rec-1",
                "kind": "record",
                "content": "Potential secret found in runbook.",
            }
        ]
    }

    try:
        build_report_context_from_query_response(query_response, max_items=0)
    except ValueError as exc:
        assert "max_items" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-positive max_items")
