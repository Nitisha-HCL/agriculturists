# Contract: Router Flow entry points

## Python API

```python
from router_flow import AgricultureRouterFlow

flow = AgricultureRouterFlow()
result: str = flow.kickoff(inputs={"user_question": "<question>"})
flow.state.plan          # RoutingPlan
flow.state.route         # "historical" | "best_practice" | "mixed" | "out_of_scope"
flow.state.part_answers  # list[PartAnswer]
flow.state.final         # FinalAnswer
```

- **Input**: `user_question` (str, required, non-empty). An empty question raises `ValueError` before any LLM call.
- **Output**: `kickoff` returns the user-facing text. That's the Summarizer's final answer (built from the Judge's verdict on historical parts plus the Farming Specialist's answers) followed by a short "How your question was split" record (FR-010), or the scope message for out-of-scope questions (FR-011).
- **Side effects**: Read-only queries against the `agriculture` and `agriculture_rag` databases; LLM calls.

## CLI

```
crewai run                       # prompts for the question
uv run kickoff "<question>"      # question as argument
```

## Pure helpers (unit-testable, no LLM)

| Function | Contract |
|---|---|
| `parse_routing_plan(text: str) -> RoutingPlan` | Extracts the first JSON object from `text`, validates it, sets `suggested_tool` from `kind`, and truncates to 4 parts. Raises `ValueError` on missing or invalid JSON. |
| `fallback_plan(question: str) -> RoutingPlan` | Mixed plan: one historical and one best_practice part, both the full question; `fallback=True`. |
| `route_label(plan: RoutingPlan) -> str` | Per the data-model rules. |
| `tools_used(output) -> list[str]` | Distinct tool names called, in order, from `output.messages`. |

## User-facing text format

```
<Summarizer's final answer>

---
Written by: Summarizer_Agent | Historical answers reviewed by: Judge_Agent (follow-ups: N)
How your question was split:
1. [historical → suggested agriculture_nl2sql] <part question> — answered by Optimistic_Agent (used: agriculture_nl2sql), Risk_Averse_Agent (used: agriculture_nl2sql)
2. [best_practice → suggested agriculture_rag_search] <part question> — answered by Farming_Specialist (used: agriculture_rag_search)
```
