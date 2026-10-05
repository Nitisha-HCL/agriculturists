# Tasks: Question Router Flow

**Input**: Design documents from `specs/001-router-flow/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/router_flow.md, quickstart.md

**Tests**: Unit tests for the pure routing helpers are included because plan.md and quickstart.md call for them. LLM behaviour is validated through the quickstart runs.

**Organization**: Tasks are grouped by user story. All paths are relative to `agriculturists/`.

## Format: `[ID] [P?] [Story] Description`

## Phase 1: Setup

- [X] T001 Add `langchain-openai` and `langchain-postgres` to dependencies and `pytest` as a dev dependency via `uv add` in pyproject.toml (research R7)
- [X] T002 Create the tests/ directory with an empty tests/__init__.py

---

## Phase 2: Foundational (blocks all stories)

- [X] T003 Add `QuestionPart` to schemas.py: `question` non-empty str; `kind` Literal["historical","best_practice"]; `suggested_tool` str derived from kind ("agriculture_nl2sql" for historical, "agriculture_rag_search" for best_practice); `place`/`crop`/`period` optional str
- [X] T004 Add `RoutingPlan` (`in_scope` bool, `parts` list[QuestionPart] with "0–4 items", `fallback` bool default False), `PartAnswer` (`part_index`, `agent_role`, `answer`, `suggested_tool`, `tools_used` list[str], `error` str|None), `FinalAnswer` (`text`, `approved_by`, `follow_up_requests` "≤ 3", `follow_ups` list of {agent, question, reply}) and `RouterState` to schemas.py per data-model.md
- [X] T005 Implement the pure helpers `parse_routing_plan`, `fallback_plan`, `route_label` and `tools_used` in router_flow.py per contracts/router_flow.md (truncate to 4 parts; `out_of_scope` when not in_scope or no parts)
- [X] T006 [P] Write unit tests in tests/test_router_flow.py covering: valid mixed JSON, JSON wrapped in prose/code fences, invalid JSON raises ValueError, 5 parts truncated to 4, suggested_tool derivation, fallback_plan shape, all four route labels, tools_used extraction from tool-call messages
- [X] T007 Implement the router-LLM step `plan_route` (prompt with worked examples, `ROUTER_LLM` env override defaulting to `ollama/qwen2.5:3b`, one retry, then `fallback_plan`) and the `@router decide_route` returning the label in router_flow.py

**Checkpoint**: `uv run pytest tests -q` passes; the router splits questions.

---

## Phase 3: User Story 1 – Mixed question answered from both sources (P1) 🎯 MVP

**Goal**: A mixed question is split, each part is answered by the responsible agents, and the Judge approves one final answer.

**Independent Test**: quickstart §3. The output ends with `Approved by: Judge_Agent` and a 2-part split record with tools used.

- [X] T008 [P] [US1] Create agents/farming_specialist.jsonc: role `Farming_Specialist`, goal/backstory on crop best practices that answers only from retrieved documents and cites sources, tools `custom:agricultureragtool` and `custom:agriculturenl2sql`, llm `qwen2.5:3b`, allow_delegation false
- [X] T009 [P] [US1] Extend tools/ask_analyst.py so `Farming_Specialist` maps to farming_specialist.jsonc; update the tool and argument descriptions to list all three agents
- [X] T010 [P] [US1] Update the agents/judge_agent.jsonc backstory: follow-ups can go to Farming_Specialist for best-practice gaps
- [X] T011 [US1] Implement agent dispatch in router_flow.py: load agents via `load_agent`; `_answer_part` builds the prompt (user question, part, suggested tool, "you make the final call on tools; state the sources you used"), runs `Agent.kickoff`, and records a `PartAnswer` with `tools_used`; agent errors are captured in `PartAnswer.error`. The prompt also requires answering only from what the tools return and saying so when information is unavailable (FR-006), and stating the assumption when the place or period is missing (edge case)
- [X] T012 [US1] Implement `@listen("mixed") handle_mixed`: historical parts go to Optimistic_Agent and Risk_Averse_Agent, best-practice parts to Farming_Specialist
- [X] T013 [US1] Implement `@listen(or_(handle_historical, handle_best_practice, handle_mixed)) judge_and_approve` in router_flow.py: fresh `ask_analyst` tool per run, Judge prompt with the question, split record and all part answers, `FinalAnswer(approved_by="Judge_Agent")`, and the user-facing text format from contracts/router_flow.md
- [X] T014 [US1] Add the `kickoff()` CLI entry (question from argv or prompt) and a `--plan-only` mode that runs the router on the 10 quickstart reference questions and prints each route label plus every part's kind and question (SC-001, SC-002) in router_flow.py

**Checkpoint**: US1 runs end to end.

---

## Phase 4: User Story 2 – Single-type question (P2)

**Independent Test**: quickstart §2 rows 5–8 route correctly; a historical-only run invokes only the two analysts.

- [X] T015 [US2] Implement `@listen("historical") handle_historical` and `@listen("best_practice") handle_best_practice` in router_flow.py, reusing the dispatch from T011/T012

---

## Phase 5: User Story 3 – Judge requests more information (P2)

**Independent Test**: In a run with a gap, the Judge's `ask_analyst` calls appear and `FinalAnswer.follow_up_requests` ≤ 3.

- [X] T016 [US3] In judge_and_approve (router_flow.py), instruct the Judge to use ask_analyst for important gaps (max 3) and to list remaining gaps under "Unresolved needs"; have ask_analyst log each request (`agent`, `question`, `reply`) in tools/ask_analyst.py and copy that log into `FinalAnswer.follow_ups`, with `follow_up_requests` = its length (FR-013, SC-006)

---

## Phase 6: User Story 4 – Out-of-scope question (P3)

**Independent Test**: quickstart §4. Scope message, no part answers.

- [X] T017 [US4] Implement `@listen("out_of_scope") handle_out_of_scope` in router_flow.py, returning the scope message with `FinalAnswer(approved_by="router")` and no agent calls

---

## Phase 7: Polish & Cross-Cutting

- [X] T018 Retire the manager crew: add a "RETIRED: replaced by router_flow.py" header comment to crew.jsonc and agents/manager_agent.jsonc
- [X] T019 Update pyproject.toml `[tool.crewai]` to type "flow" without `definition`, add `[project.scripts] kickoff = "router_flow:kickoff"`, and add router_flow.py and schemas.py to the wheel `only-include`
- [X] T020 Update the main.ipynb run cell to use `AgricultureRouterFlow().kickoff(inputs={"user_question": ...})` instead of `load_crew`
- [X] T021 Run quickstart §1–§4 and record the results in specs/001-router-flow/quickstart.md

## Phase 8: Change Request – Judge reviews historical answers, Summarizer writes the final answer (2026-10-05)

**Goal**: Optimistic + Risk-Averse answers → Judge (verdict); Judge verdict + Farming_Specialist answers → Summarizer → user (FR-007, FR-008, FR-009, FR-009a, FR-010, SC-003).

**Independent Test**: The mixed quickstart run shows `judge_verdict` set, `written_by == "Summarizer_Agent"`, and a split record; a best-practice-only run has `judge_verdict` None and still ends with `Written by: Summarizer_Agent`.

- [X] T022 [P] Create agents/summarizer_agent.jsonc: role `Summarizer_Agent`, no tools, writes the final answer only from the Judge's verdict and the Farming_Specialist's answers, carries over unresolved needs, adds nothing new, llm `qwen2.5:3b`
- [X] T023 [P] Update the agents/judge_agent.jsonc goal/backstory: reviews the Optimistic and Risk-Averse answers to historical parts and gives a verdict (does not write the user's final answer); follow-ups go only to Optimistic_Agent or Risk_Averse_Agent
- [X] T024 Update `FinalAnswer` in schemas.py: replace `approved_by` with `written_by` (`"Summarizer_Agent"` or `"router"`) and add `judge_verdict` (str | None)
- [X] T025 In router_flow.py, replace `judge_and_approve` with `@listen(or_(handle_historical, handle_mixed)) judge_historical`, which sends only historical PartAnswers to the Judge, stores its verdict and follow-ups, and returns the verdict
- [X] T026 In router_flow.py, add `@listen(or_(judge_historical, handle_best_practice)) summarize`, which runs Summarizer_Agent on the Judge's verdict plus the best-practice PartAnswers, sets `FinalAnswer(written_by=..., judge_verdict=...)`, and returns the text with the contract footer
- [X] T027 Update handle_out_of_scope (`written_by="router"`), the main.ipynb inspection cell, the router_flow.py module docstring diagram, and contracts/router_flow.md references
- [X] T028 Re-run the unit tests and the mixed and best-practice-only end-to-end runs; record the results in quickstart.md

## Phase 9: Answer-quality fixes (2026-10-05, from the Phase 8 validation)

**Goal**: Stop fabricated figures from reaching the user, keep agents to their own part, and make sure unresolved needs survive summarization (FR-014, FR-015, FR-009a, SC-007).

**Independent Test**: Rerun the quickstart §3 mixed question. Every yield figure in the final answer appears in the database results recorded in `part_answers[*].tool_results`, and the Judge's unresolved needs appear in the final answer.

- [X] T029 Add `ToolResult` (`tool`, `output`) and `PartAnswer.tool_results` to schemas.py; add a pure `tool_results(output)` helper in router_flow.py that reads `role: "tool"` messages (output truncated to 4000 chars), plus unit tests in tests/test_router_flow.py
- [X] T030 Pass each analyst's raw tool results into the Judge prompt in `judge_historical` (router_flow.py), with the instruction to check every figure against them and flag unsupported ones (FR-014)
- [X] T031 Scope `_answer_part`'s prompt to the assigned part only, with the full question as background (FR-015) in router_flow.py
- [X] T032 [P] Switch the `llm` of agents/judge_agent.jsonc and agents/summarizer_agent.jsonc to `gemini/gemini-3.8-flash`
- [X] T033 Add a pure `unresolved_needs(text)` helper and an FR-009a guard in `summarize`: if the Judge listed needs and the Summarizer's answer says None or omits them, append the Judge's needs; with unit tests
- [X] T034 Rerun the unit tests and the quickstart §3 mixed run; record the results in quickstart.md

## Phase 10: Best-practice answer verification (2026-10-05, from the Phase 9 validation)

**Goal**: Stop misread or off-crop advice (e.g. "sulfuric acid" for the document's MgSO4, chickpea pests in a cotton answer) from reaching the user (FR-016, FR-017, SC-008).

**Independent Test**: Rerun quickstart §3. The Farming_Specialist's `tool_results` contain only cotton (or crop-neutral) sources, and every pest recommendation in the final answer appears in those passages.

- [X] T035 Add crop filtering to tools/agricultureragtool.py: optional `crop` argument; otherwise detect the crop from the question; source→crop map (`iepf101.pdf` → rice, plus filename keywords); over-fetch and keep only matching or crop-neutral sources; fall back to unfiltered results with a note when nothing matches. Expose the pure helpers `detect_crop` and `source_crop`
- [X] T036 [P] Unit tests for `detect_crop`, `source_crop` and the result filter in tests/test_rag_crop_filter.py (no database)
- [X] T037 Pass the Farming_Specialist's retrieved passages (`tool_results`) to the Summarizer in `summarize` (router_flow.py), instructing it to keep only advice the passages support and list the rest under 'Unsupported advice removed' (FR-016)
- [X] T038 Pass `part.crop` to the Farming_Specialist prompt as the crop to use with agriculture_rag_search (router_flow.py), and update the farming_specialist.jsonc backstory to mention the `crop` argument
- [X] T039 Rerun the unit tests and the quickstart §3 mixed run; record the results in quickstart.md

## Dependencies & Execution Order

- Setup (T001–T002) → Foundational (T003–T007) → US1 (T008–T014) → US2 (T015), US3 (T016), US4 (T017) → Polish (T018–T021) → Change request (T022–T028; T022/T023 in parallel, then T024 → T025 → T026 → T027 → T028)
- US2–US4 depend only on Foundational + T011 (dispatch) + T013 (judge), and can be done in any order.
- **Parallel**: T006 alongside T007; T008, T009 and T010 together (different files).

## Implementation Strategy

MVP = Phases 1–3 (mixed questions end to end). Then add US2, US3 and US4, each validated by its quickstart section, and finish with Polish, which switches the project's entry point.
