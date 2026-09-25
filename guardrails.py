from crewai.tasks.task_output import TaskOutput


def validate_optimistic_agent_output(result: TaskOutput):
    """Validate the optimistic agent's output."""

    output = result.json_dict

    if not output:
        return False, "The output must be valid JSON."

    required_fields = [
        "historical_yield",
        "typical_outcome",
        "favorable_factors",
        "potential_upside",
        "evidence",
        "uncertainty",
        "summary",
    ]

    missing = [
        field for field in required_fields
        if not output.get(field)
    ]

    if missing:
        return False, (
            f"Missing required fields: {', '.join(missing)}. "
            "Return all required fields."
        )

    if not output.get("evidence"):
        return False, "The analysis must contain supporting evidence."

    if not output.get("uncertainty"):
        return False, "The analysis must explicitly state uncertainty."

    return True

def validate_risk_averse_agent_output(result: TaskOutput):
    """Validate the risk-averse agent's output."""

    output = result.json_dict

    if not output:
        return False, "The output must be valid JSON."

    required_fields = [
        "verdict",
        "risk_factors",
        "risk_level",
        "common_findings",
        "additional_info",
    ]

    missing = [
        field for field in required_fields
        if not output.get(field)
    ]

    if missing:
        return False, (
            f"Missing required fields: {', '.join(missing)}. "
            "Return all required fields."
        )

    if not output.get("risk_factors"):
        return False, "At least one evidence-based risk factor is required."

    if not output.get("additional_info"):
        return False, (
            "State what additional information is needed, "
            "or explicitly state that no additional information is needed."
        )

    return True

def validate_judge_agent_output(result: TaskOutput):
    """Validate the final judge output."""

    output = result.json_dict

    if not output:
        return False, "The output must be valid JSON."

    required_fields = [
        "summary_of_evidence",
        "comparison_of_perspectives",
        "key_uncertainties",
        "final_conclusion",
    ]

    missing = [
        field for field in required_fields
        if not output.get(field)
    ]

    if missing:
        return False, (
            f"Missing required fields: {', '.join(missing)}. "
            "Return all required fields."
        )

    if not output.get("comparison_of_perspectives"):
        return False, (
            "The final assessment must explicitly compare "
            "the optimistic and risk-averse perspectives."
        )

    if not output.get("key_uncertainties"):
        return False, (
            "The final assessment must explicitly identify "
            "important uncertainties."
        )

    if not output.get("final_conclusion"):
        return False, "A final evidence-based conclusion is required."

    return True