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