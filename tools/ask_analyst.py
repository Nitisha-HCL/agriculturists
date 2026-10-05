"""Lets the Judge request more information from an analyst or the Farming
Specialist mid-task.

The Judge's agent run has no other way to reach its coworkers. This tool loads
the requested agent from its agents/<name>.jsonc definition (same role,
backstory, LLM and tools) and runs the request with Agent.kickoff(). Every
request and reply is kept in `log` so the router flow can record follow-ups.

Only one BaseTool subclass may live in this module: the custom tool loader
instantiates the first one it finds.
"""

import re
from pathlib import Path
from typing import Any

from crewai.project.json_loader import load_agent
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
ANALYSTS = {
    "optimisticagent": "optimistic_agent.jsonc",
    "riskaverseagent": "risk_averse_agent.jsonc",
    "farmingspecialist": "farming_specialist.jsonc",
}
AGENT_NAMES = "'Optimistic_Agent', 'Risk_Averse_Agent' or 'Farming_Specialist'"
# Caps follow-up requests per tool instance (one per question in the router
# flow) so a small model cannot loop forever.
MAX_REQUESTS = 3


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


class AskAnalystInput(BaseModel):
    analyst: str = Field(
        ...,
        description=(
            "Who to ask: 'Optimistic_Agent' (historical data: typical outcomes, upside), "
            "'Risk_Averse_Agent' (historical data: downside, variability) or "
            "'Farming_Specialist' (crop best practices)."
        ),
    )
    question: str = Field(
        ...,
        description=(
            "The specific information you need, as a plain-English data question, e.g. "
            "'What was the rainfall in Nanded in the years cotton yield fell below 100 kg/ha?'"
        ),
    )
    context: str = Field(
        default="",
        description="The user's question and why you need this information.",
    )


class AskAnalystTool(BaseTool):
    name: str = "ask_analyst"
    description: str = (
        "Request more information from an analyst or the Farming Specialist when an "
        "important disagreement or gap cannot be settled from the answers you have. "
        "They query the agriculture database or the crop best-practice documents and "
        "report back. Ask one specific question per call; "
        f"at most {MAX_REQUESTS} requests are allowed per run."
    )
    args_schema: type[BaseModel] = AskAnalystInput

    _agents: dict[str, Any] = PrivateAttr(default_factory=dict)
    _requests: int = PrivateAttr(default=0)
    _log: list[dict[str, str]] = PrivateAttr(default_factory=list)

    @property
    def log(self) -> list[dict[str, str]]:
        """Each request made so far: {"agent", "question", "reply"}."""
        return list(self._log)

    def _run(self, analyst: str, question: str, context: str = "") -> str:
        key = _normalize(analyst)
        if key not in ANALYSTS:
            return f"Unknown agent. Use {AGENT_NAMES}."
        if self._requests >= MAX_REQUESTS:
            return (
                f"The limit of {MAX_REQUESTS} information requests has been reached. "
                "Finish your assessment and list anything still open under Unresolved needs."
            )
        self._requests += 1

        if key not in self._agents:
            self._agents[key] = load_agent(AGENTS_DIR / ANALYSTS[key])

        prompt = (
            "The Judge needs more information before reaching a conclusion.\n"
            f"Context: {context or 'None given.'}\n"
            f"Request: {question}\n\n"
            "Use the agriculture_nl2sql tool for historical data, or the "
            "agriculture_rag_search tool for crop best practices, passing a plain-English "
            "question, never SQL. Report what was returned (with the source document for "
            "anything from agriculture_rag_search) and what it shows about the request. If "
            "neither source contains the information, say so plainly. Do not invent information."
        )
        try:
            reply = self._agents[key].kickoff(prompt).raw
        except Exception as exc:
            reply = f"The agent could not complete the request: {exc}"
        self._log.append({"agent": analyst, "question": question, "reply": reply})
        return reply
