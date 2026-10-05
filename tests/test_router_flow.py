"""Unit tests for the router flow's pure helpers (no LLM, no database)."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from router_flow import (  # noqa: E402
    fallback_plan,
    format_split_record,
    parse_routing_plan,
    plan_question,
    route_label,
    tools_used,
)
from schemas import PartAnswer, QuestionPart, RoutingPlan  # noqa: E402

MIXED = {
    "in_scope": True,
    "parts": [
        {"question": "How has cotton yield in Nanded changed?", "kind": "historical", "place": "Nanded", "crop": "cotton", "period": None},
        {"question": "How should cotton pests be managed?", "kind": "best_practice", "place": None, "crop": "cotton", "period": None},
    ],
}


def plan_of(*kinds, in_scope=True):
    return RoutingPlan(in_scope=in_scope, parts=[QuestionPart(question=f"q{i}", kind=k) for i, k in enumerate(kinds)])


def test_parse_valid_mixed_json():
    plan = parse_routing_plan(json.dumps(MIXED))
    assert [p.kind for p in plan.parts] == ["historical", "best_practice"]
    assert plan.parts[0].place == "Nanded"
    assert plan.fallback is False


def test_parse_json_wrapped_in_prose_and_fences():
    text = "Here is the plan:\n```json\n" + json.dumps(MIXED) + "\n```\nDone."
    assert len(parse_routing_plan(text).parts) == 2


@pytest.mark.parametrize("text", ["", "no json here", '{"in_scope": true, "parts": [{"kind": "weather"}]}', "{not json}"])
def test_parse_invalid_raises_value_error(text):
    with pytest.raises(ValueError):
        parse_routing_plan(text)


def test_parts_truncated_to_four():
    data = {"in_scope": True, "parts": [{"question": f"q{i}", "kind": "historical"} for i in range(5)]}
    assert len(parse_routing_plan(json.dumps(data)).parts) == 4


def test_suggested_tool_derived_from_kind():
    plan = parse_routing_plan(json.dumps(MIXED))
    assert plan.parts[0].suggested_tool == "agriculture_nl2sql"
    assert plan.parts[1].suggested_tool == "agriculture_rag_search"


def test_suggested_tool_ignores_llm_value():
    part = QuestionPart(question="q", kind="best_practice", suggested_tool="something_else")
    assert part.suggested_tool == "agriculture_rag_search"


def test_fallback_plan_shape():
    plan = fallback_plan("Why is my cotton failing?")
    assert plan.fallback and plan.in_scope
    assert [p.kind for p in plan.parts] == ["historical", "best_practice"]
    assert all(p.question == "Why is my cotton failing?" for p in plan.parts)


@pytest.mark.parametrize(
    "plan, label",
    [
        (plan_of("historical", "historical"), "historical"),
        (plan_of("best_practice"), "best_practice"),
        (plan_of("historical", "best_practice"), "mixed"),
        (plan_of(in_scope=False), "out_of_scope"),
        (plan_of(), "out_of_scope"),
    ],
)
def test_route_labels(plan, label):
    assert route_label(plan) == label


def test_plan_question_retries_then_falls_back():
    calls = []

    class BadLLM:
        def call(self, prompt):
            calls.append(prompt)
            return "sorry, no JSON"

    plan = plan_question("Q?", BadLLM())
    assert len(calls) == 2
    assert plan.fallback


def test_plan_question_uses_llm_output():
    class GoodLLM:
        def call(self, prompt):
            return json.dumps(MIXED)

    assert route_label(plan_question("Q?", GoodLLM())) == "mixed"


def test_tools_used_from_native_tool_calls():
    output = SimpleNamespace(messages=[
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "agriculture_nl2sql", "arguments": "{}"}}]},
        {"role": "tool", "name": "agriculture_nl2sql", "content": "rows"},
        {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "agriculture_rag_search", "arguments": "{}"}}]},
    ])
    assert tools_used(output) == ["agriculture_nl2sql", "agriculture_rag_search"]


def test_tools_used_from_react_text_and_unknown_names_ignored():
    output = SimpleNamespace(messages=[
        {"role": "assistant", "content": "Thought: look it up\nAction: agriculture_rag_search\nAction Input: {}"},
        {"role": "assistant", "content": "Action: made_up_tool"},
    ])
    assert tools_used(output) == ["agriculture_rag_search"]


def test_tools_used_no_messages():
    assert tools_used(SimpleNamespace(messages=[])) == []
    assert tools_used(object()) == []


def test_split_record_lists_parts_and_answerers():
    plan = parse_routing_plan(json.dumps(MIXED))
    answers = [
        PartAnswer(part_index=0, agent_role="Optimistic_Agent", answer="a", suggested_tool="agriculture_nl2sql", tools_used=["agriculture_nl2sql"]),
        PartAnswer(part_index=1, agent_role="Farming_Specialist", answer="b", suggested_tool="agriculture_rag_search"),
    ]
    record = format_split_record(plan, answers)
    assert "1. [historical → suggested agriculture_nl2sql]" in record
    assert "Optimistic_Agent (used: agriculture_nl2sql)" in record
    assert "Farming_Specialist (used: no tool)" in record


# --- Phase 9: raw tool results and unresolved-needs guard -------------------

from router_flow import tool_results, unresolved_needs  # noqa: E402


def test_tool_results_reads_tool_messages_only():
    output = SimpleNamespace(messages=[
        {"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "agriculture_nl2sql"}}]},
        {"role": "tool", "name": "agriculture_nl2sql", "content": "SQL used: SELECT ...\nRows returned: 2"},
        {"role": "tool", "name": "made_up_tool", "content": "ignored"},
        {"role": "assistant", "content": "The yield was 122.8 kg/ha."},
    ])
    results = tool_results(output)
    assert [(r.tool, r.output) for r in results] == [("agriculture_nl2sql", "SQL used: SELECT ...\nRows returned: 2")]


def test_tool_results_truncates_long_output():
    output = SimpleNamespace(messages=[{"role": "tool", "name": "agriculture_rag_search", "content": "x" * 5000}])
    text = tool_results(output)[0].output
    assert text.endswith("...[truncated]") and len(text) < 4100


VERDICT = """### Verdict
Yields rose from 92.96 kg/ha (2014) to 313.95 kg/ha (2016).

### Unresolved needs
1. Rainfall data for 2014-2017 to explain the 2017 drop.
2. Yield data after 2017 is not in the database.

### Confidence
Moderate."""


def test_unresolved_needs_extracts_markdown_section():
    needs = unresolved_needs(VERDICT)
    assert needs.startswith("1. Rainfall data")
    assert "2. Yield data after 2017" in needs
    assert "Confidence" not in needs and "Moderate" not in needs


@pytest.mark.parametrize("text", [
    "### Unresolved needs\nNone\n\n### Sources\n- a.pdf",
    "Unresolved needs:\nNone.\n\nSources:\n1. a.pdf",
    "### Unresolved needs\n- None\n",
    "No such section here.",
    "",
])
def test_unresolved_needs_none_or_absent(text):
    assert unresolved_needs(text) == ""


def test_unresolved_needs_inline_and_bold_heading():
    assert unresolved_needs("**Unresolved needs:** rainfall for 2017\n\n**Sources**\n- db") == "rainfall for 2017"


from router_flow import has_content  # noqa: E402


@pytest.mark.parametrize("text", [
    "Sources used:\n- agriculture_nl2sql",
    "Sources used:\nagriculture_nl2sql:get_cotton_yield_Nanded_2014_2023_results.json",
    "Answer:\n\nSources used: agriculture_nl2sql",
    "",
])
def test_has_content_false_for_sources_only(text):
    assert not has_content(text)


def test_has_content_true_for_real_answer():
    text = ("Answer: Cotton yield in Nanded rose from 92.96 kg/ha in 2014 to 313.95 kg/ha in 2016, "
            "then fell to 187.31 kg/ha in 2017.\nSources used: agriculture_nl2sql")
    assert has_content(text)
