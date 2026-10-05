# Define the Pydantic model for the blog
from pydantic import BaseModel


class OptimisticAnalysisOutput(BaseModel):
    historical_yield: str
    typical_outcome: str
    favorable_factors: str
    potential_upside: str
    evidence: str
    uncertainty: str
    summary: str

class RiskAnalysisOutput(BaseModel):
    verdict: str
    risk_factors: str
    risk_level: str
    common_findings: str
    additional_info: str

class JudgeAnalysisOutput(BaseModel):
    summary_of_evidence: str
    comparison_of_perspectives: str
    additional_information_retrieved: str
    key_uncertainties: str
    final_conclusion: str

# ---------------------------------------------------------------------------
# Router flow models (specs/001-router-flow/data-model.md)
# ---------------------------------------------------------------------------

from typing import Literal

from pydantic import Field, field_validator, model_validator

MAX_PARTS = 4
SUGGESTED_TOOLS = {
    "historical": "agriculture_nl2sql",
    "best_practice": "agriculture_rag_search",
}


class QuestionPart(BaseModel):
    question: str
    kind: Literal["historical", "best_practice"]
    suggested_tool: str = ""
    place: str | None = None
    crop: str | None = None
    period: str | None = None

    @field_validator("question")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be empty")
        return value.strip()

    @model_validator(mode="after")
    def _derive_suggested_tool(self) -> "QuestionPart":
        self.suggested_tool = SUGGESTED_TOOLS[self.kind]
        return self


class RoutingPlan(BaseModel):
    in_scope: bool
    parts: list[QuestionPart] = Field(default_factory=list)
    fallback: bool = False

    @field_validator("parts")
    @classmethod
    def _cap_parts(cls, parts: list[QuestionPart]) -> list[QuestionPart]:
        return parts[:MAX_PARTS]


class ToolResult(BaseModel):
    tool: str
    output: str


class PartAnswer(BaseModel):
    part_index: int
    agent_role: str
    answer: str
    suggested_tool: str
    tools_used: list[str] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)  # raw tool output (FR-014)
    error: str | None = None


class FollowUp(BaseModel):
    agent: str
    question: str
    reply: str


class FinalAnswer(BaseModel):
    text: str
    written_by: str  # "Summarizer_Agent", or "router" for out-of-scope messages
    judge_verdict: str | None = None  # None when the question had no historical part
    follow_up_requests: int = 0
    follow_ups: list[FollowUp] = Field(default_factory=list)


class RouterState(BaseModel):
    user_question: str = ""
    plan: RoutingPlan | None = None
    route: str | None = None
    part_answers: list[PartAnswer] = Field(default_factory=list)
    judge_verdict: str | None = None
    follow_ups: list[FollowUp] = Field(default_factory=list)
    final: FinalAnswer | None = None
