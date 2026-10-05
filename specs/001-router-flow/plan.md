# Implementation Plan: Question Router Flow

**Branch**: `001-router-flow` | **Date**: 2026-10-05 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-router-flow/spec.md`

## Summary

Replace the manager-led hierarchical crew with a CrewAI `Flow` that implements the Router pattern. A router step splits the user's question into up to 4 parts, each marked *historical* or *best_practice*. A `@router` then picks one of four routes: `historical`, `best_practice`, `mixed` or `out_of_scope`. The route handlers send each historical part to both the Optimistic and Risk-Averse analysts, and each best-practice part to a new Farming Specialist, with a suggested tool. Each agent decides for itself which tools to use. The Judge reviews only the two analysts' historical answers and can ask either analyst for more information (`ask_analyst`, capped at 3). A new Summarizer agent then combines the Judge's verdict with the Farming Specialist's answers into the final answer returned to the user. Best-practice-only questions skip the Judge (change request, 2026-10-05). See [research.md](research.md) for the verified technical decisions.

## Technical Context

**Language/Version**: Python 3.13 (project venv)

**Primary Dependencies**: crewai 1.15.22 (Flow, Agent, LLM, json_loader); existing custom tools `agriculturenl2sql`, `agricultureragtool` and `ask_analyst`; new: `langchain-openai` and `langchain-postgres` (required by the RAG tool, currently missing); dev: `pytest`

**Storage**: PostgreSQL. The `agriculture` database holds historical records; `agriculture_rag` holds the guidance documents. Both are read-only.

**Testing**: pytest unit tests for the pure routing helpers; router-only reference run and end-to-end runs per [quickstart.md](quickstart.md)

**Target Platform**: Windows workstation, local Ollama, Jupyter notebook and CLI

**Project Type**: CrewAI project (single project, flat layout)

**Performance Goals**: None beyond the spec. A local 3B model means minutes per end-to-end run.

**Constraints**: Local `qwen2.5:3b` for the agents and the router (router overridable via `ROUTER_LLM`). Max 4 parts and 3 Judge follow-ups per question. Flow methods must be sync (R3).

**Scale/Scope**: Single user, one question per run

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

`.specify/memory/constitution.md` is still the unfilled template, so it has no ratified principles to check against. Gate: **PASS (no constraints defined)**. The project's de facto rules from `AGENTS.md` were applied instead:

- Use `crewai.LLM` and the `crewai.flow` decorators. ✅
- Listener method names differ from route labels (`handle_*`). ✅
- Don't disable observability. ✅
- Tools come from `crewai.tools`. ✅

Post-design re-check: PASS. Running `/speckit-constitution` is recommended before the next feature.

## Project Structure

### Documentation (this feature)

```text
specs/001-router-flow/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/router_flow.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks output
```

### Source Code (repository root = agriculturists/)

```text
router_flow.py                 # NEW: AgricultureRouterFlow, pure helpers, kickoff() CLI entry
schemas.py                     # EXTEND: QuestionPart, RoutingPlan, PartAnswer, FinalAnswer, RouterState
agents/
├── farming_specialist.jsonc   # NEW: Farming_Specialist (RAG + NL2SQL tools)
├── summarizer_agent.jsonc     # NEW: Summarizer_Agent (no tools; writes the final answer)
├── judge_agent.jsonc          # UPDATE: reviews historical answers only; follow-ups to the two analysts
├── optimistic_agent.jsonc     # unchanged
├── risk_averse_agent.jsonc    # unchanged
└── manager_agent.jsonc        # RETIRED: header comment, no longer referenced
tools/
└── ask_analyst.py             # UPDATE: add Farming_Specialist target
crew.jsonc                     # RETIRED: header comment, no longer the entry point
pyproject.toml                 # UPDATE: type="flow", kickoff script, deps, wheel includes
main.ipynb                     # UPDATE: run cell uses AgricultureRouterFlow
tests/
└── test_router_flow.py        # NEW: unit tests for the pure helpers
```

**Structure Decision**: Keep the existing flat CrewAI JSON-project layout. The flow is a single module at the root next to `schemas.py`, and agents stay as `agents/*.jsonc` loaded with `load_agent`, so their role/goal/backstory remain the single source of truth.

## Complexity Tracking

No constitution violations to justify.
