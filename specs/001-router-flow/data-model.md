# Data Model: Question Router Flow

Phase 1 output. All models are Pydantic classes in `schemas.py`; the flow state lives on `AgricultureRouterFlow.state`.

## QuestionPart

| Field | Type | Rules |
|---|---|---|
| `question` | str | Non-empty, self-contained sub-question |
| `kind` | `"historical"` \| `"best_practice"` | Required |
| `suggested_tool` | str | Derived from `kind`: `agriculture_nl2sql` for historical, `agriculture_rag_search` for best_practice (FR-003) |
| `place` | str \| None | Optional |
| `crop` | str \| None | Optional |
| `period` | str \| None | Optional |

## RoutingPlan

| Field | Type | Rules |
|---|---|---|
| `in_scope` | bool | False ⇒ `parts` empty |
| `parts` | list[QuestionPart] | 0–4 items (FR-002); truncated to 4 |
| `fallback` | bool | True when the LLM output could not be parsed and the fallback plan was used |

**Derived `route` label** (pure function `route_label(plan)`):

- `out_of_scope` when `not in_scope` or no parts
- `historical` when every part is historical
- `best_practice` when every part is best_practice
- `mixed` otherwise

## PartAnswer

| Field | Type | Rules |
|---|---|---|
| `part_index` | int | Index into `RoutingPlan.parts` |
| `agent_role` | str | `Optimistic_Agent`, `Risk_Averse_Agent` or `Farming_Specialist` |
| `answer` | str | The agent's raw answer |
| `suggested_tool` | str | Copied from the part |
| `tools_used` | list[str] | Tool names actually called, from the agent's messages (FR-005, SC-004); empty list means no tool was called |
| `error` | str \| None | Set when the agent run failed; the answer then explains the failure |
| `tool_results` | list[ToolResult] | Raw output of each tool call (`tool`, `output`, truncated to 4000 chars), from the agent's `role: "tool"` messages (FR-014) |

A historical part yields two PartAnswers (optimistic and risk-averse); a best-practice part yields one.

## FinalAnswer

| Field | Type | Rules |
|---|---|---|
| `text` | str | The Summarizer's final answer |
| `written_by` | str | `"Summarizer_Agent"` for every in-scope answer; `"router"` for out-of-scope messages |
| `judge_verdict` | str \| None | The Judge's review of the historical answers; None when the question had no historical part |
| `follow_up_requests` | int | Number of `ask_analyst` calls made by the Judge (≤ 3, SC-006); 0 when the Judge did not run |
| `follow_ups` | list[FollowUp] | Each Judge request: `agent`, `question`, `reply` (FR-013) |

## RouterState (flow state)

| Field | Type |
|---|---|
| `user_question` | str (input) |
| `plan` | RoutingPlan \| None |
| `route` | str \| None |
| `part_answers` | list[PartAnswer] |
| `judge_verdict` | str \| None (set by judge_historical) |
| `follow_ups` | list[FollowUp] (Judge's ask_analyst log) |
| `final` | FinalAnswer \| None |

### State transitions

```
start ─plan_route─▶ planned ─decide_route─▶ {historical | best_practice | mixed | out_of_scope}
  historical ─handle_historical─▶ answered ─judge_historical─▶ judged ─summarize─▶ done (final set)
  mixed      ─handle_mixed──────▶ answered ─judge_historical─▶ judged ─summarize─▶ done (final set)
  best_practice ─handle_best_practice─▶ answered ──────────────────────▶ summarize ─▶ done (final set)
  out_of_scope ─handle_out_of_scope─▶ declined (final set, written_by="router")
```
