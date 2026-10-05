"""Question Router Flow (specs/001-router-flow).

Replaces the manager-led hierarchical crew with the CrewAI Router pattern:

    plan_route ──▶ decide_route ─┬─ "historical"    ─▶ handle_historical    ─┐
                                 ├─ "mixed"         ─▶ handle_mixed         ─┴─▶ judge_historical ─┐
                                 ├─ "best_practice" ─▶ handle_best_practice ───────────────────────┴─▶ summarize
                                 └─ "out_of_scope"  ─▶ handle_out_of_scope

Historical parts go to both Optimistic_Agent and Risk_Averse_Agent; best-practice
parts go to Farming_Specialist. Each gets a suggested tool but decides itself.
The Judge reviews only the two analysts' historical answers and may ask either
of them for more information (ask_analyst, max 3). The Summarizer combines the
Judge's verdict with the Farming_Specialist's answers into the final answer;
best-practice-only questions skip the Judge.

Flow methods are deliberately sync: the flow runtime runs them in a worker
thread with no event loop, so Agent.kickoff() (and the Judge's nested
ask_analyst kickoffs) run synchronously. In an async method Agent.kickoff()
would return a coroutine instead.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from crewai import LLM
from crewai.flow.flow import Flow, listen, or_, router, start
from crewai.project.json_loader import load_agent
from dotenv import load_dotenv

from schemas import (
    FinalAnswer,
    FollowUp,
    PartAnswer,
    QuestionPart,
    RouterState,
    RoutingPlan,
    ToolResult,
)

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
AGENTS_DIR = PROJECT_DIR / "agents"

ROUTER_LLM = os.environ.get("ROUTER_LLM", "ollama/qwen2.5:3b")
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

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


def router_llm() -> LLM:
    kwargs = {"base_url": OLLAMA_BASE_URL} if ROUTER_LLM.startswith("ollama/") else {}
    return LLM(model=ROUTER_LLM, temperature=0, **kwargs)


def plan_question(question: str, llm: Any = None) -> RoutingPlan:
    """Ask the router LLM to split the question; one retry, then the fallback plan."""
    llm = llm or router_llm()
    for _ in range(2):
        try:
            return parse_routing_plan(str(llm.call(ROUTER_PROMPT + question)))
        except ValueError:
            continue
    return fallback_plan(question)


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


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------

class AgricultureRouterFlow(Flow[RouterState]):
    """Routes parts to agents; the Judge reviews historical answers, the Summarizer writes the final answer."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agents: dict[str, Any] = {}

    # -- helpers ------------------------------------------------------------

    def _agent(self, name: str) -> Any:
        if name not in self._agents:
            self._agents[name] = load_agent(AGENTS_DIR / f"{name}.jsonc")
        return self._agents[name]

    def _answer_part(self, index: int, part: QuestionPart, agent_name: str) -> PartAnswer:
        agent = self._agent(agent_name)
        details = ", ".join(
            f"{label}: {value}"
            for label, value in (("place", part.place), ("crop", part.crop), ("period", part.period))
            if value
        )
        prompt = (
            f"Your task: answer ONLY this question: {part.question}\n"
            + (f"Details: {details}\n" if details else "")
            + f"(Background only: it is one part of the user's question \"{self.state.user_question}\". "
            "Other agents handle the other parts; do not answer them.)\n"
            + f"Suggested tool: {part.suggested_tool}. This is only a suggestion; you make the "
            "final call on which of your tools to use, and you may use more than one.\n"
            + (
                f"When you call agriculture_rag_search, pass crop=\"{part.crop}\" so only "
                f"documents about {part.crop} are searched.\n"
                if part.crop
                else ""
            )
            + "\n"
            "Answer only from what your tools return. Report only figures and years that appear "
            "in the tool results; never extend a series beyond the years returned. If the "
            "information is not available, say so plainly; do not invent information. If the "
            "place or period is not specified, state the assumption you made.\n\n"
            "Format your reply as:\n"
            "Answer: the figures or recommendations from your tool results, and your analysis "
            "of them from your own perspective (several sentences).\n"
            "Sources used: the tools (and documents) you actually used."
        )
        try:
            output = agent.kickoff(prompt)
            used, results = tools_used(output), tool_results(output)
            if not has_content(output.raw):
                # Small models sometimes return only the "Sources used" line; ask once more.
                output = agent.kickoff(
                    prompt + "\n\nYour previous reply contained no answer, only a sources line. "
                    "Write the full Answer section this time."
                )
                used = list(dict.fromkeys(used + tools_used(output)))
                results = results + tool_results(output)
            return PartAnswer(
                part_index=index,
                agent_role=agent.role,
                answer=output.raw,
                suggested_tool=part.suggested_tool,
                tools_used=used,
                tool_results=results,
            )
        except Exception as exc:
            return PartAnswer(
                part_index=index,
                agent_role=agent.role,
                answer=f"This part could not be answered: {type(exc).__name__}: {exc}",
                suggested_tool=part.suggested_tool,
                error=str(exc),
            )

    def _answer_parts(self, kind: str) -> None:
        for index, part in enumerate(self.state.plan.parts):
            if part.kind != kind:
                continue
            agent_names = HISTORICAL_AGENTS if kind == "historical" else (BEST_PRACTICE_AGENT,)
            for agent_name in agent_names:
                self.state.part_answers.append(self._answer_part(index, part, agent_name))

    # -- router ---------------------------------------------------------------

    @start()
    def plan_route(self) -> RoutingPlan:
        question = (self.state.user_question or "").strip()
        if not question:
            raise ValueError("user_question must not be empty")
        self.state.plan = plan_question(question)
        return self.state.plan

    @router(plan_route)
    def decide_route(self) -> str:
        self.state.route = route_label(self.state.plan)
        return self.state.route

    # -- route handlers (names differ from labels, per AGENTS.md) --------------

    @listen("historical")
    def handle_historical(self) -> None:
        self._answer_parts("historical")

    @listen("best_practice")
    def handle_best_practice(self) -> None:
        self._answer_parts("best_practice")

    @listen("mixed")
    def handle_mixed(self) -> None:
        self._answer_parts("historical")
        self._answer_parts("best_practice")

    @listen("out_of_scope")
    def handle_out_of_scope(self) -> str:
        self.state.final = FinalAnswer(text=SCOPE_MESSAGE, written_by="router")
        return SCOPE_MESSAGE

    # -- judge: reviews the two analysts' historical answers only ------------

    def _answers_text(self, kind: str, with_tool_results: bool = False) -> str:
        parts = self.state.plan.parts
        blocks = []
        for a in self.state.part_answers:
            if parts[a.part_index].kind != kind:
                continue
            block = (
                f"### Part {a.part_index + 1}: {parts[a.part_index].question}\n"
                f"Answered by {a.agent_role} (used {', '.join(a.tools_used) or 'no tool'})\n{a.answer}"
            )
            if with_tool_results:
                raw = "\n".join(f"[{r.tool}] {r.output}" for r in a.tool_results)
                source = "database" if kind == "historical" else "document search"
                block += (
                    f"\n\nRaw results the {source} actually returned to {a.agent_role}:\n"
                    + (raw or "(none: this analyst made no tool call, so none of its figures are verified)")
                )
            blocks.append(block)
        return "\n\n".join(blocks)

    @listen(or_(handle_historical, handle_mixed))
    def judge_historical(self) -> str:
        # Fresh Judge per question, so its ask_analyst cap and log are per question.
        judge = load_agent(AGENTS_DIR / "judge_agent.jsonc")
        ask_tool = next((t for t in judge.tools if t.name == "ask_analyst"), None)

        prompt = (
            f"User question: {self.state.user_question}\n\n"
            "The Optimistic and Risk-Averse analysts answered the historical-data part(s) of "
            "this question. Below each answer are the raw results the database actually "
            f"returned to that analyst:\n\n{self._answers_text('historical', with_tool_results=True)}\n\n"
            "You are the Judge. Review only these historical answers and give your verdict. "
            "Other parts of the question (best practices) are handled separately; do not "
            "address them.\n"
            "1. Check every figure and year in each answer against that analyst's raw database "
            "results. Any figure or year not present in the raw results is unsupported: leave it "
            "out of your verdict and name it under 'Unsupported claims'. The raw results are the "
            "ground truth, not the analysts' text.\n"
            "2. State the key verified figures. Compare the two analysts' answers: where they agree, "
            "where they genuinely disagree, and which claims the data supports. If their "
            "figures are identical, say so; do not invent differences.\n"
            "3. If an important gap or disagreement cannot be settled from these answers, call "
            "the ask_analyst tool (analyst: Optimistic_Agent or Risk_Averse_Agent) with one "
            "specific plain-English data question. Do not just list questions you would ask; "
            "call the tool. At most 3 requests.\n"
            "4. Anything still not settled goes under 'Unresolved needs': what is missing and "
            "why it matters. Do not fill gaps with general knowledge.\n"
            "5. Separate established findings from estimates, and state your confidence.\n"
            "Write your verdict with these sections: Verdict, Evidence, Unsupported claims (or "
            "None), Unresolved needs (or None), Confidence."
        )
        output = judge.kickoff(prompt)

        self.state.judge_verdict = output.raw
        self.state.follow_ups = [FollowUp(**entry) for entry in (ask_tool.log if ask_tool else [])]
        return output.raw

    # -- summarizer: Judge verdict + Farming_Specialist answers -> user ---------

    @listen(or_(judge_historical, handle_best_practice))
    def summarize(self) -> str:
        summarizer = self._agent("summarizer_agent")
        verdict = self.state.judge_verdict
        # FR-016: include the passages the Farming_Specialist actually retrieved.
        best_practice = self._answers_text("best_practice", with_tool_results=True)
        judge_needs = unresolved_needs(verdict) if verdict else ""

        sections = []
        if verdict:
            sections.append(f"## Judge's verdict on the historical data\n{verdict}")
        if best_practice:
            sections.append(
                "## Farming Specialist's best-practice answers, each followed by the document "
                f"passages it actually retrieved\n{best_practice}"
            )
        prompt = (
            f"User question: {self.state.user_question}\n\n"
            + "\n\n".join(sections)
            + "\n\nWrite the final answer to the user's question using only the material above. "
            "Answer every part of the question; keep numbers, years, places and source "
            "citations exactly as given; do not add facts or recommendations of your own. "
            + (
                "Check every best-practice recommendation against the retrieved document "
                "passages: include it only if the passages support it, using the passages' "
                "wording for chemicals, doses and crops (the passages are the ground truth, not "
                "the Farming Specialist's text). Leave out anything unsupported, or about a "
                "different crop than the one asked about, and name it briefly under 'Unsupported "
                "advice removed'. Cite only documents whose passages you used. "
                if best_practice
                else ""
            )
            + (
                f"Under 'Unresolved needs', include all of the Judge's unresolved needs:\n{judge_needs}\n"
                if judge_needs
                else "Under 'Unresolved needs', carry over any open issues (or write None). "
            )
            + "Use only figures from the Judge's verdict, never its unsupported claims. "
            "Use these sections: Answer, Details, "
            + ("Unsupported advice removed (or None), " if best_practice else "")
            + "Unresolved needs, Sources."
        )
        output = summarizer.kickoff(prompt)
        text = output.raw
        # FR-009a guard: the Judge's unresolved needs must reach the user.
        if judge_needs and not unresolved_needs(text):
            text += f"\n\nUnresolved needs (from the Judge's verdict):\n{judge_needs}"

        self.state.final = FinalAnswer(
            text=text,
            written_by=summarizer.role,
            judge_verdict=verdict,
            follow_up_requests=len(self.state.follow_ups),
            follow_ups=self.state.follow_ups,
        )
        reviewed = (
            f" | Historical answers reviewed by: Judge_Agent (follow-ups: {len(self.state.follow_ups)})"
            if verdict
            else ""
        )
        return (
            f"{text}\n\n---\nWritten by: {summarizer.role}{reviewed}\n"
            f"How your question was split:\n"
            f"{format_split_record(self.state.plan, self.state.part_answers)}"
        )


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

REFERENCE_QUESTIONS = [
    ("How has cotton yield in Nanded changed over the last 10 years, and what pest management practices are recommended for cotton?", "mixed"),
    ("What was rice yield in Durg in 2015, and how should rice be irrigated?", "mixed"),
    ("Which Maharashtra districts had the highest chickpea yields after 2010, and how is chickpea wilt managed?", "mixed"),
    ("How did rainfall affect maize yield in Durg, and what fertilizer schedule is recommended for kharif maize?", "mixed"),
    ("What was rice yield in Durg in 2015?", "historical"),
    ("How has cotton area in Nanded changed since 2000?", "historical"),
    ("How should chickpea wilt be managed?", "best_practice"),
    ("When should kharif maize be irrigated?", "best_practice"),
    ("What is the capital of France?", "out_of_scope"),
    ("Recommend a good laptop for programming.", "out_of_scope"),
]


def run_plan_only() -> int:
    """Router-only check of the quickstart reference questions (SC-001, SC-002)."""
    llm = router_llm()
    correct = 0
    for number, (question, expected) in enumerate(REFERENCE_QUESTIONS, start=1):
        plan = plan_question(question, llm)
        label = route_label(plan)
        correct += label == expected
        mark = "OK " if label == expected else "XX "
        print(f"{mark}{number:2}. expected={expected:13} got={label:13} {question}")
        for part in plan.parts:
            print(f"        - {part.kind:13} {part.question}")
    print(f"\n{correct}/{len(REFERENCE_QUESTIONS)} route labels correct (target ≥ 9)")
    return correct


def kickoff() -> None:
    """CLI entry: `uv run kickoff "<question>"`, `crewai run`, or `--plan-only`."""
    args = sys.argv[1:]
    if args == ["--plan-only"]:
        run_plan_only()
        return
    question = " ".join(args).strip() or input("Your agricultural question: ").strip()
    print(AgricultureRouterFlow().kickoff(inputs={"user_question": question}))


if __name__ == "__main__":
    kickoff()
