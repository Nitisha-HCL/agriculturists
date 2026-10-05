"""Lets the Judge request more information from an analyst mid-task.

In a hierarchical crew, an agent the manager delegates to runs with only its own
tools: it cannot call back to the manager or reach its coworkers. This tool
gives the Judge that channel. It loads the requested analyst from its
agents/<name>.jsonc definition (same role, backstory, LLM and database tool as
in the crew) and runs the request with Agent.kickoff().

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
}
# Caps follow-up requests per crew run so a small model cannot loop forever.
MAX_REQUESTS = 3


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


class AskAnalystInput(BaseModel):
    analyst: str = Field(
        ...,
        description="Who to ask: 'Optimistic_Agent' (typical outcomes, upside) or 'Risk_Averse_Agent' (downside, variability).",
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
        "Request more information from an analyst when an important disagreement or "
        "gap cannot be settled from the analyses you have. The analyst queries the "
        "agriculture database and reports back. Ask one specific question per call; "
        f"at most {MAX_REQUESTS} requests are allowed per run."
    )
    args_schema: type[BaseModel] = AskAnalystInput

    _agents: dict[str, Any] = PrivateAttr(default_factory=dict)
    _requests: int = PrivateAttr(default=0)

    def _run(self, analyst: str, question: str, context: str = "") -> str:
        key = _normalize(analyst)
        if key not in ANALYSTS:
            return "Unknown analyst. Use 'Optimistic_Agent' or 'Risk_Averse_Agent'."
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
            "Use the agriculture_nl2sql tool to get the data, passing it a plain-English "
            "question, never SQL. Report the data returned and what it shows about the "
            "request. If the database does not contain the information, say so plainly. "
            "Do not invent information."
        )
        try:
            return self._agents[key].kickoff(prompt).raw
        except Exception as exc:
            return f"The analyst could not complete the request: {exc}"
