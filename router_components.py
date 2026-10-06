from pathlib import Path
from typing import Any
import json
import re

from schemas import (
    FinalAnswer,
    FollowUp,
    PartAnswer,
    QuestionPart,
    RouterState,
    RoutingPlan,
    ToolResult,
)



HISTORICAL_AGENTS = ("optimistic_agent", "risk_averse_agent")
BEST_PRACTICE_AGENT = "farming_specialist"
KNOWN_TOOLS = ("agriculture_nl2sql", "agriculture_rag_search", "ask_analyst")

SCOPE_MESSAGE = (
    "I can only answer agricultural questions: historical crop data for a "
    "particular place, crop or period (yields, area, rainfall, fertilizer by "
    "year), and general best practices for growing crops (irrigation, pests, "
    "diseases, nutrients, sowing). Please rephrase your question along those lines."
)

# Plain JSON with worked examples: qwen2.5:3b returns empty plans with
# structured-output mode but splits correctly this way (research.md R2).
ROUTER_PROMPT = """Split the user's question into self-contained parts and return JSON only.
kind "historical": recorded data for a particular place, crop or period (yield, area, rainfall, fertilizer by year).
kind "best_practice": general crop guidance (irrigation, pests, diseases, nutrients, sowing, varieties).
Use at most 4 parts. If the question is not about agriculture, return {"in_scope": false, "parts": []}.

Example question: What was maize yield in Durg in 2010 and how much water does maize need?
Example JSON: {"in_scope": true, "parts": [{"question": "What was maize yield in Durg in 2010?", "kind": "historical", "place": "Durg", "crop": "maize", "period": "2010"}, {"question": "How much water does maize need?", "kind": "best_practice", "place": null, "crop": "maize", "period": null}]}

Example question: Who won the football world cup?
Example JSON: {"in_scope": false, "parts": []}

Question: """


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_router_flow.py)
# ---------------------------------------------------------------------------

def parse_routing_plan(text: str) -> RoutingPlan:
    """Parse the router LLM's reply into a RoutingPlan; raise ValueError if invalid."""
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("no JSON object in router output")
    try:
        return RoutingPlan.model_validate(json.loads(match.group(0)))
    except Exception as exc:
        raise ValueError(f"invalid routing plan: {exc}") from exc


def fallback_plan(question: str) -> RoutingPlan:
    """Used when the router output cannot be parsed: let both kinds of agent try."""
    return RoutingPlan(
        in_scope=True,
        parts=[
            QuestionPart(question=question, kind="historical"),
            QuestionPart(question=question, kind="best_practice"),
        ],
        fallback=True,
    )






def route_label(plan: RoutingPlan) -> str:
    if not plan.in_scope or not plan.parts:
        return "out_of_scope"
    kinds = {part.kind for part in plan.parts}
    if kinds == {"historical"}:
        return "historical"
    if kinds == {"best_practice"}:
        return "best_practice"
    return "mixed"


def tools_used(output: Any) -> list[str]:
    """Distinct tool names an agent actually called, in call order, from its messages."""
    names: list[str] = []

    def add(name: Any) -> None:
        if isinstance(name, str) and name in KNOWN_TOOLS and name not in names:
            names.append(name)

    for msg in getattr(output, "messages", None) or []:
        if not isinstance(msg, dict):
            msg = getattr(msg, "__dict__", {})
        if msg.get("role") == "tool":
            add(msg.get("name"))
        for call in msg.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else getattr(call, "function", None)
            if isinstance(function, dict):
                add(function.get("name"))
            else:
                add(getattr(function, "name", None))
        content = msg.get("content")
        if isinstance(content, str):
            # ReAct-style text tool calls ("Action: agriculture_nl2sql")
            for name in re.findall(r"Action:\s*([A-Za-z_]+)", content):
                add(name)
    return names


MIN_ANSWER_CHARS = 40


def has_content(answer: str) -> bool:
    """True when an agent's answer says something beyond its 'Sources used' line."""
    body = re.split(r"(?im)^\W*sources used\b", answer or "")[0]
    body = re.sub(r"(?im)^\W*answer\s*:\**", "", body)
    return len(body.strip()) >= MIN_ANSWER_CHARS


MAX_TOOL_OUTPUT_CHARS = 4000


def tool_results(output: Any) -> list[ToolResult]:
    """Raw output of each tool call, from the agent's role="tool" messages (FR-014)."""
    results = []
    for msg in getattr(output, "messages", None) or []:
        if not isinstance(msg, dict):
            msg = getattr(msg, "__dict__", {})
        if msg.get("role") == "tool" and msg.get("name") in KNOWN_TOOLS:
            text = str(msg.get("content") or "")
            if len(text) > MAX_TOOL_OUTPUT_CHARS:
                text = text[:MAX_TOOL_OUTPUT_CHARS] + " ...[truncated]"
            results.append(ToolResult(tool=msg["name"], output=text))
    return results


# A section heading such as "### Confidence", "**Sources**" or "Confidence:".
_HEADING_RE = re.compile(r"^\s*(#+\s*[A-Za-z].*|\*\*[A-Za-z][A-Za-z ]{1,40}\*\*\s*:?\s*|[A-Z][A-Za-z ]{1,40}:\s*)$")


def unresolved_needs(text: str) -> str:
    """The content of an 'Unresolved needs' section, or "" when absent or None."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        head = line.strip().lstrip("#*- ").strip()
        if not head.lower().startswith("unresolved needs"):
            continue
        body = [head[len("unresolved needs"):].lstrip("*: ").strip()]
        for nxt in lines[i + 1:]:
            if _HEADING_RE.match(nxt):
                break
            body.append(nxt)
        content = "\n".join(b for b in body if b.strip()).strip()
        if content.strip(" .*-").lower() in {"", "none"}:
            return ""
        return content
    return ""


def format_split_record(plan: RoutingPlan, answers: list[PartAnswer]) -> str:
    lines = []
    for index, part in enumerate(plan.parts):
        by = [
            f"{a.agent_role} (used: {', '.join(a.tools_used) or 'no tool'})"
            for a in answers
            if a.part_index == index
        ]
        lines.append(
            f"{index + 1}. [{part.kind} → suggested {part.suggested_tool}] {part.question}"
            + (f" — answered by {', '.join(by)}" if by else "")
        )
    if plan.fallback:
        lines.append("(The question could not be split automatically; both kinds of agent answered it whole.)")
    return "\n".join(lines)