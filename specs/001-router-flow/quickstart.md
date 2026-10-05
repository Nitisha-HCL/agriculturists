# Quickstart: Question Router Flow

## Prerequisites

- Ollama running at `http://localhost:11434` with `qwen2.5:3b`
- PostgreSQL with the `agriculture` (crop_yields) and `agriculture_rag` (rag_documents) databases
- `OPENAI_API_KEY` in the environment (RAG embeddings); `GEMINI_API_KEY` only if `ROUTER_LLM` is set to a Gemini model
- Dependencies installed: `uv sync` in `agriculturists/`

## 1. Unit tests (no LLM, seconds)

```
uv run pytest tests -q
```

Expected: all tests pass. They cover JSON parsing, the fallback plan, route labels, the 4-part cap, suggested tools, and tool-usage extraction.

## 2. Router-only check (SC-001, SC-002; LLM, ~1 min)

```
uv run python -m router_flow --plan-only
```

This runs only the router on the 10 reference questions below and prints each route label. Expected: at least 9 of 10 labels match.

| # | Question | Expected route |
|---|---|---|
| 1 | How has cotton yield in Nanded changed over the last 10 years, and what pest management practices are recommended for cotton? | mixed |
| 2 | What was rice yield in Durg in 2015, and how should rice be irrigated? | mixed |
| 3 | Which Maharashtra districts had the highest chickpea yields after 2010, and how is chickpea wilt managed? | mixed |
| 4 | How did rainfall affect maize yield in Durg, and what fertilizer schedule is recommended for kharif maize? | mixed |
| 5 | What was rice yield in Durg in 2015? | historical |
| 6 | How has cotton area in Nanded changed since 2000? | historical |
| 7 | How should chickpea wilt be managed? | best_practice |
| 8 | When should kharif maize be irrigated? | best_practice |
| 9 | What is the capital of France? | out_of_scope |
| 10 | Recommend a good laptop for programming. | out_of_scope |

## 3. End-to-end (P1; LLM + databases, several minutes)

```
uv run kickoff "How has cotton yield in Nanded changed over the last 10 years, and what pest management practices are recommended for cotton?"
```

Expected:

- The output ends with `Written by: Summarizer_Agent | Historical answers reviewed by: Judge_Agent` and a split record with 2 parts; `flow.state.final.judge_verdict` is set.
- The historical part was answered by both analysts, and the best-practice part by Farming_Specialist.
- Every answer lists the tools it used (SC-004).

In `main.ipynb`, the run cell does the same through `AgricultureRouterFlow().kickoff(...)`. Inspect `flow.state` afterwards.

## 4. Out of scope (SC-005)

```
uv run kickoff "What is the capital of France?"
```

Expected: a scope message; `flow.state.part_answers` is empty.

## 5. Best-practice only (skips the Judge)

```
uv run kickoff "How should chickpea wilt be managed?"
```

Expected: route `best_practice`; only Farming_Specialist answers; `flow.state.final.judge_verdict` is None; the output ends with `Written by: Summarizer_Agent`.

## Validation results (2026-10-05, qwen2.5:3b everywhere)

| Check | Result |
|---|---|
| §1 Unit tests | 21 passed |
| §2 Router-only (SC-001, SC-002) | 10/10 route labels correct; all 4 mixed questions split into the expected historical + best_practice parts |
| §3 End-to-end mixed (P1) | Route `mixed`, 2 parts. Historical part answered by Optimistic_Agent and Risk_Averse_Agent (both used agriculture_nl2sql); best-practice part answered by Farming_Specialist (used agriculture_rag_search). Output ends with `Approved by: Judge_Agent` plus the split record (SC-003, SC-004). 730 s. |
| §4 Out of scope (SC-005) | Scope message, 0 part answers, approved_by `router`. 16 s. |
| Judge follow-ups (US3, SC-006) | 0 requests in the §3 run; the cap was not exercised end to end. The Judge wrote its intended follow-ups as text instead of calling ask_analyst. |

**Answer-quality issues observed** (model behaviour, not flow wiring):
- Optimistic_Agent also answered the pest-management part, because its prompt includes the full user question.
- The Judge invented a difference between the two analysts' identical yield figures and attributed the Farming_Specialist's pest advice to the analysts.
- The Judge listed follow-up questions as text instead of calling ask_analyst.
- Farming_Specialist cited one chickpea document page for a cotton answer (retrieval noise).

## Validation results after change request (2026-10-05, Judge → Summarizer; qwen2.5:3b everywhere)

| Check | Result |
|---|---|
| Unit tests | 21 passed |
| §5 Best-practice only | Route `best_practice`; only Farming_Specialist answered (agriculture_rag_search); `judge_verdict` None (Judge skipped); `Written by: Summarizer_Agent`. 240 s. |
| §3 Mixed | Route `mixed`; the historical part was answered by both analysts (agriculture_nl2sql) and judged by Judge_Agent with **1 real ask_analyst follow-up** (US3 now exercised, SC-006 respected); the best-practice part was answered by Farming_Specialist; `Written by: Summarizer_Agent \| Historical answers reviewed by: Judge_Agent (follow-ups: 1)`. 226 s. |

**Answer-quality issues observed** (model behaviour; the flow wiring is correct):
- **Fabricated data**: Optimistic_Agent listed cotton yields for 2018–2023, but the database ends in 2017 (the database tool returned 2014–2017; Risk_Averse_Agent reported exactly that). The Judge did not catch it: it said both analysts agreed on 2014–2023 and asked about 2024.
- The Summarizer wrote "Unresolved needs: None" although the Judge listed three, which violates FR-009a. It also dropped the yield figures.
- The chickpea-wilt answer was mostly general chickpea agronomy (lime, weeds, nipping) rather than wilt management.

## Validation results after Phase 9 (2026-10-05; Judge and Summarizer on Gemini, other agents on qwen2.5:3b)

| Check | Result |
|---|---|
| Unit tests | 35 passed |
| §3 Mixed, run 1 | Final answer had only verified figures (2014–2017); the Judge used 1 follow-up to confirm there is no 2018–2023 data, and that need reached the final answer. **Regression**: both analysts returned only a "Sources used" line. Fixed by an explicit Answer/Sources format plus one retry when an answer is empty (`has_content`). 390 s. |
| §3 Mixed, run 2 | Both analysts gave real answers with only database figures. The Judge flagged one unsupported wording ("an average of 92.96") under Unsupported claims and made 1 follow-up. The final answer's yield figures all match the database rows; the unresolved need (no data after 2017) is carried over (SC-007 met for historical data). 116 s. |

**Remaining issue (best-practice path, not covered by FR-014):** Farming_Specialist (qwen2.5:3b) turned the document's "spray MgSO4 1%" into "a 1% solution of sulfuric acid" for leaf reddening, and the Summarizer passed it on. It also mixed in chickpea-page pests (gram pod borer, semilooper) and an unrelated government scheme. Nothing currently verifies best-practice answers against the retrieved documents.

## Validation results after Phase 10 (2026-10-05; crop filter + Summarizer checks advice against passages)

| Check | Result |
|---|---|
| Unit tests | 53 passed (18 new for the crop filter) |
| Live crop filter | A cotton question returns only BN_Cotton.pdf passages, whether `crop` is passed or detected from the question |
| §3 Mixed (SC-007, SC-008) | 119 s. Farming_Specialist retrieved only BN_Cotton.pdf. The final answer has no chickpea document, pests or "sulfuric acid"; leaf reddening now reads "MgSO4 1%" as in the document. All 30 chemicals, doses and varieties spot-checked appear in the retrieved passages. The Summarizer listed 2 removed items under "Unsupported advice removed" (carbendazim called an insecticide; a wrong Endosulfan dose). Yield figures are the 4 database rows; the 2018–2023 gap is carried over; 1 Judge follow-up. |

**Minor**: the Judge (Gemini) suggested an outside data source (state agriculture department / DES reports) in its unresolved need. That is a pointer rather than a factual claim, but it does come from general knowledge.
