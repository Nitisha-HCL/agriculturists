

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

from router_components import HISTORICAL_AGENTS, BEST_PRACTICE_AGENT, KNOWN_TOOLS, SCOPE_MESSAGE, ROUTER_PROMPT
from router_components import has_content, tool_results, unresolved_needs, fallback_plan, format_split_record, parse_routing_plan, route_label, tools_used
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


class AgricultureRouterFlow(Flow[RouterState]):
    #Divides the prompt into historical data and best-practice parts
    #routes each to the appropriate agent(s), and combines their answers. 
    #The optimistic and risk-averse agents provide their viewpoints on the historical databse information
    #The Judge reviews the historical answers
    #The Farming Specialist provides best-practice answers
    #The Summarizer writes the final answer.

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._agents: dict[str, Any] = {}


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
        
        # the Judge's unresolved needs must reach the user.
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





def kickoff() -> None:
    """CLI entry: `uv run kickoff "<question>"`, `crewai run`, or `--plan-only`."""
    args = sys.argv[1:]
    question = " ".join(args).strip() or input("Your agricultural question: ").strip()
    print(AgricultureRouterFlow().kickoff(inputs={"user_question": question}))


if __name__ == "__main__":
    kickoff()
