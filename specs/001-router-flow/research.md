# Research: Question Router Flow

Phase 0 output for [plan.md](plan.md). Findings were verified against the installed packages (crewai 1.15.22, crewai_cli 1.15.22) and by probing the local models, not taken from memory.

## R1. Router mechanism

- **Decision**: A Python `Flow` subclass using `@start` → `@router` → `@listen("<label>")` handlers, with labels `historical`, `best_practice`, `mixed`, `out_of_scope`, and a final `@listen(or_(...))` Judge step.
- **Rationale**: This is CrewAI's native Router pattern (`crewai.flow.flow.router`). A router returns one label per run, so a mixed question gets its own `mixed` label whose handler runs both kinds of part. That way each handler fires exactly once and the Judge step can listen with `or_` across the three answer handlers, without double-firing.
- **Alternatives considered**: (a) Two listeners `or_("historical","mixed")` and `or_("best_practice","mixed")` plus `and_` for the Judge: the Judge trigger condition differs by route, which `and_`/`or_` cannot express cleanly. (b) A declarative (YAML/JSON) `FlowDefinition`: this is supported by `crewai run`, but it can't express the per-part dispatch loop and tool-usage capture without custom Python anyway.
- **Constraint** (from AGENTS.md): handler method names must differ from the labels they listen to, so they use the `handle_*` prefix.

## R2. Splitting the question (router LLM)

- **Decision**: Plain-JSON prompt with two worked examples, parsed into a Pydantic `RoutingPlan`; default model `ollama/qwen2.5:3b`, overridable via `ROUTER_LLM` (e.g. `gemini/gemini-3.8-flash`).
- **Rationale**: Probe on 4 reference questions (mixed, historical, best-practice, out-of-scope):
  - `qwen2.5:3b` with `LLM.call(response_model=...)` returned an empty out-of-scope plan for all 4 (unusable).
  - `qwen2.5:3b` with plain JSON + examples got 4/4 correct.
  - `gemini/gemini-3.8-flash` with plain JSON + examples got 4/4 correct.
- **Failure handling**: One retry on parse failure. If it fails again, fall back to a `mixed` plan with the full question as one historical part and one best-practice part. The answering agents decide on sources anyway (FR-005), so the user still gets an answer.
- **Alternatives considered**: Structured output (`response_model`) was rejected because of the probe result. An Agent-based router was rejected because a single LLM call is simpler and cheaper.

## R3. Running agents inside the flow

- **Decision**: Flow methods are **sync**. Agents are loaded from their existing `agents/*.jsonc` files with `crewai.project.json_loader.load_agent` and run with `Agent.kickoff(prompt)`.
- **Rationale**: The flow runtime runs sync methods via `asyncio.to_thread`, so they have no running event loop. In that context `Agent.kickoff` runs synchronously, and so do the tools it calls, including `ask_analyst`'s nested `Agent.kickoff`. In an `async` method, `Agent.kickoff` would return a coroutine (`is_inside_event_loop()`), which would break `ask_analyst`'s `.raw` access. `Flow.kickoff()` itself detects Jupyter's running loop and runs in a worker thread, so `main.ipynb` can call it directly.
- **Alternatives considered**: Async methods with `kickoff_async` plus `asyncio.gather` for concurrency were rejected: there's no performance requirement, and they break nested tool-driven kickoffs.

## R4. Recording sources actually used (FR-005, SC-004)

- **Decision**: Derive tool usage from `LiteAgentOutput.messages`, looking for tool-call names `agriculture_nl2sql` and `agriculture_rag_search`, and map them to the source names "historical records" and "guidance documents". Also ask each agent to state its sources in its answer.
- **Rationale**: This records what the agent actually did, not just what it says it did. The messages field is present on `LiteAgentOutput` in 1.15.22.

## R5. Entry point and retiring the manager crew (FR-012)

- **Decision**: New module `router_flow.py` exposing `AgricultureRouterFlow` and a `kickoff()` function. `pyproject.toml` switches `[tool.crewai]` to `type = "flow"`, drops `definition = "crew.jsonc"`, and adds the script `kickoff = "router_flow:kickoff"`, so `crewai run` starts the flow (crewai_cli `_run_flow_project` → `uv run kickoff`). `main.ipynb` calls the flow instead of `load_crew`.
- **Retirement**: `crew.jsonc` and `agents/manager_agent.jsonc` are no longer referenced. They stay on disk (spec assumption) and get a header comment marking them retired.
- **Alternatives considered**: Deleting the files was rejected because deletion is irreversible and the spec allows keeping them.

## R6. Farming Specialist tools

- **Decision**: New `agents/farming_specialist.jsonc` with tools `custom:agricultureragtool` and `custom:agriculturenl2sql`, on `qwen2.5:3b` like the analysts.
- **Rationale**: FR-005 says the agent makes the final call on sources, so it must have both available. The router's suggestion is advisory.

## R7. Dependencies

- **Finding**: `custom:agricultureragtool` imports `langchain_openai` and `langchain_postgres`, which are **not installed** in the project venv, so loading any agent with that tool fails today.
- **Decision**: Add `langchain-openai` and `langchain-postgres` to the project dependencies (`uv add`), and `pytest` as a dev dependency for the unit tests.

## R8. Judge follow-ups to the Farming Specialist (FR-008)

- **Decision**: Extend `tools/ask_analyst.py` with `Farming_Specialist` → `farming_specialist.jsonc`, and keep the 3-request cap. Create a fresh tool instance per Judge run so the cap applies per question (SC-006).

## R9. Gemini for the Judge and Summarizer (Phase 9)

- **Decision**: The Judge and Summarizer run on `gemini/gemini-3.8-flash`; the router, analysts and Farming Specialist stay on local `qwen2.5:3b`.
- **Rationale**: In the Phase 8 validation, `qwen2.5:3b` as Judge accepted fabricated years and as Summarizer dropped the Judge's unresolved needs. Both jobs require careful cross-checking and faithful summarisation.
- **Dependency**: CrewAI's native Gemini provider needs the `google-genai` extra, now declared as `crewai[google-genai,tools]` in pyproject.toml. It was missing from the project venv, though present in the notebook's parent venv.
