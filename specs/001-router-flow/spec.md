# Feature Specification: Question Router Flow

**Feature Branch**: `001-router-flow`

**Created**: 2026-10-05

**Status**: Draft

**Input**: User description: "Add a crewai Flow so as to implement the Router pattern. The router needs to divide the question into parts which are resolved from historical data in a particular place vs general agricultural best practices and send them to the respective agents, along with a suggestion on which tool to use. Again the sub agent takes the final call and returns the answer, which is sent to the judge who may ask for more information. Finally the final answer gets approved from the judge and sent to the user."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Mixed question answered from both sources (Priority: P1)

A farmer or advisor asks one question that needs both kinds of knowledge, for example "How has cotton yield in Nanded changed over the last 10 years, and what pest management practices are recommended for cotton?". The system splits it into a historical-data part ("cotton yield in Nanded over the last 10 years") and a best-practice part ("recommended pest management for cotton"), sends the historical part to both the Optimistic and Risk-Averse analysts and the best-practice part to the Farming Specialist, each with a suggestion of which knowledge source fits, has the Judge review the two historical answers, then has the Summarizer combine the Judge's verdict with the Farming Specialist's answer, and returns that single final answer to the user.

**Why this priority**: This is the core value of the router: one question, correctly decomposed, each part answered from the right source, and one final answer back. It exercises every step of the flow.

**Independent Test**: Ask a question with one clear historical part and one clear best-practice part; verify the routing record shows two parts with the expected types and suggested sources, that each part was answered, that the Judge reviewed the historical answers, and that the user receives one Summarizer-written answer covering both.

**Acceptance Scenarios**:

1. **Given** a question with a historical part about a named place and a general best-practice part, **When** the user submits it, **Then** the system produces exactly one historical part and one best-practice part, each with a suggested knowledge source.
2. **Given** the parts have been routed, **When** each responsible agent answers, **Then** each agent may use a different knowledge source than the one suggested, and its answer states which source it actually used.
3. **Given** both analysts have answered the historical part, **When** the Judge reviews their answers, **Then** the Judge produces a verdict on the historical part.
4. **Given** the Judge's verdict and the Farming Specialist's answer, **When** the Summarizer combines them, **Then** the user receives a single answer, written by the Summarizer, that addresses both parts.

---

### User Story 2 - Single-type question routed to one source (Priority: P2)

A user asks a question that is purely historical ("What was rice yield in Durg in 2015?") or purely about practice ("How should chickpea wilt be managed?"). The router recognises there is only one part and sends only that part, so no unnecessary work is done on the other source.

**Why this priority**: Most everyday questions are of one type; handling them without invoking the unused path keeps answers fast and avoids irrelevant content.

**Independent Test**: Submit one purely historical and one purely best-practice question; verify each produces exactly one part of the matching type and only that part is answered (a historical-only question is reviewed by the Judge; a best-practice-only question skips the Judge), followed by the Summarizer's final answer.

**Acceptance Scenarios**:

1. **Given** a purely historical question about a place, **When** submitted, **Then** only a historical part is produced and answered.
2. **Given** a purely best-practice question, **When** submitted, **Then** only a best-practice part is produced and answered.

---

### User Story 3 - Judge requests more information before giving its verdict (Priority: P2)

After reviewing the part answers, the Judge finds an important gap or disagreement (for example, yields dropped sharply in one year and no answer explains why). Before giving its verdict, the Judge asks a specific follow-up question to the appropriate agent, receives the answer, and then gives a verdict that includes it. If a gap still cannot be closed, the verdict (and so the final answer) lists it as an unresolved need instead of guessing.

**Why this priority**: The user explicitly wants the Judge to be able to ask for more information; it raises answer quality but the flow is still valuable without it.

**Independent Test**: Submit a question whose part answers leave an obvious gap; verify the Judge's follow-up request and its answer appear in the run record, and the final answer either uses the follow-up or lists the gap as unresolved.

**Acceptance Scenarios**:

1. **Given** historical answers with an important unexplained gap, **When** the Judge reviews them, **Then** the Judge issues at least one follow-up request to the Optimistic or Risk-Averse analyst before giving its verdict.
2. **Given** the Judge has used its follow-up allowance, **When** gaps remain, **Then** the Judge's verdict lists them under unresolved needs, the Summarizer carries them into the final answer, and no information is invented.

---

### User Story 4 - Out-of-scope question declined (Priority: P3)

A user asks something that is neither agricultural historical data nor agricultural practice (for example, "What is the capital of France?"). The router recognises this and returns a short, polite message explaining what the system can answer, without invoking any agents.

**Why this priority**: Protects users from fabricated answers and saves effort, but is not core value.

**Independent Test**: Submit an unrelated question; verify no agent work occurs and the user receives a scope message.

**Acceptance Scenarios**:

1. **Given** a non-agricultural question, **When** submitted, **Then** the user receives a scope message and no part is routed to any agent.

---

### Edge Cases

- A historical part names no place (e.g. "How has cotton yield changed?"): the part is still routed as historical, and the answering agent states that it covered all available places or asks for none and notes the assumption.
- A part names a place or crop that does not exist in the historical records: the answering agent reports that no data was found rather than inventing figures; the Judge's verdict and the Summarizer's final answer reflect this in the final answer.
- The question contains more than one historical part (e.g. two districts) or more than one best-practice part: each is routed as its own part (up to a maximum of 4 parts per question).
- The router is unsure whether a part is historical or best-practice: it chooses the closer type and records the suggestion; the answering agent may still use the other source.
- A knowledge source is unavailable or fails: the affected part's answer reports the failure; the Summarizer's final answer states which part could not be answered.
- The Judge's follow-up requests would loop indefinitely: follow-ups are capped (at most 3 per question).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST accept a single natural-language agricultural question from the user and return a single final answer.
- **FR-002**: System MUST split the question into one or more parts (maximum 4), each classified as either *historical* (answerable from recorded historical agricultural data for a particular place, crop, or period) or *best-practice* (answerable from general agricultural guidance documents).
- **FR-003**: System MUST attach to each part a suggested knowledge source (historical records for historical parts, guidance documents for best-practice parts) and, where present, the place, crop, and period it concerns.
- **FR-004**: System MUST route each historical part to both perspective analysts (the Optimistic analyst and the Risk-Averse analyst), each answering independently, and each best-practice part to a new Farming Specialist agent responsible for crop best practices.
- **FR-005**: The answering agent MUST make the final decision on which knowledge source(s) to use for its part; the suggestion is advisory, and the answer MUST state the source(s) actually used.
- **FR-006**: The answering agent MUST base its answer only on what the knowledge sources return and MUST state explicitly when the information is not available.
- **FR-007**: System MUST send the Optimistic and Risk-Averse answers to historical parts, together with the original question and the routing record, to the Judge. Best-practice answers do not go to the Judge. If the question has no historical part, the Judge step is skipped.
- **FR-008**: The Judge MUST be able to request additional information from the Optimistic or Risk-Averse analyst before giving its verdict, limited to at most 3 follow-up requests per question.
- **FR-009**: The Judge MUST produce a verdict on the historical part(s): a synthesis of both analysts' answers, with any needs still open after follow-ups listed as unresolved rather than filled with unsupported content.
- **FR-009a**: A Summarizer agent MUST produce the final answer from the Judge's verdict (when present) and the Farming Specialist's answers (when present), without adding information that is in neither, and carrying over the unresolved needs. If the Summarizer omits the Judge's unresolved needs, the system MUST add them to the final answer.
- **FR-014**: The Judge MUST receive, alongside each analyst's answer, the raw results the analyst's knowledge-source queries actually returned, and MUST treat any figure not present in those results as unsupported (excluded from the verdict and flagged).
- **FR-015**: Each answering agent MUST answer only its assigned part; the full question is given as background only.
- **FR-016**: The Summarizer MUST receive the guidance-document passages the Farming Specialist actually retrieved and MUST include only best-practice advice those passages support; unsupported advice is left out and named under 'Unsupported advice removed'.
- **FR-017**: Guidance-document searches about a specific crop MUST return only passages from documents about that crop (or documents not tied to any crop); if none match, the search says so and falls back to unfiltered results.
- **FR-010**: System MUST return only the Summarizer's final answer to the user, along with a brief record of how the question was split.
- **FR-011**: System MUST detect out-of-scope (non-agricultural) questions and return a scope message without routing any parts.
- **FR-012**: The new flow MUST replace the manager-led crew as the system's entry point: the router takes over the manager's delegating role, and the manager-led crew is retired.
- **FR-013**: Each run MUST produce an inspectable record of: the parts and their types, suggested sources, sources actually used, each part answer, each Judge follow-up and its reply, the Judge's verdict, and the final answer with who wrote it.

### Key Entities

- **Question**: The user's original natural-language question.
- **Question Part**: A self-contained sub-question with a type (historical or best-practice), a suggested knowledge source, and optional place, crop, and period.
- **Routing Plan**: The ordered set of Question Parts for one Question, plus an overall route label (historical only, best-practice only, mixed, or out of scope).
- **Part Answer**: One responsible agent's answer to one Question Part (a historical part has two: optimistic and risk-averse), including the knowledge source(s) actually used and any stated gaps.
- **Follow-up Request**: A Judge's question to a responsible agent, and the reply.
- **Judge Verdict**: The Judge's synthesis of the historical answers, including unresolved needs.
- **Final Answer**: The Summarizer's response delivered to the user, combining the Judge Verdict and the best-practice answers, including unresolved needs and a marker of who wrote it.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a set of 10 reference questions (4 mixed, 2 historical-only, 2 best-practice-only, 2 out-of-scope), the router assigns the correct route label for at least 9 of 10.
- **SC-002**: For the mixed reference questions, every expected part is present in the routing plan with the correct type in at least 3 of 4 questions.
- **SC-003**: 100% of delivered answers to in-scope questions are written by the Summarizer; no historical answer reaches the Summarizer without passing through the Judge.
- **SC-004**: 100% of part answers name the knowledge source(s) actually used.
- **SC-005**: Out-of-scope questions return the scope message without any agent work in 100% of cases.
- **SC-006**: No run makes more than 3 Judge follow-up requests.
- **SC-008**: In the reference mixed run, the cotton pest-management advice cites no chickpea document, and every recommendation in the final answer appears in the retrieved cotton passages.
- **SC-007**: In the reference mixed run, the final answer contains no historical figure that is absent from the database results, and the Judge's unresolved needs appear in the final answer.

## Assumptions

- The two knowledge sources are the existing ones: the historical crop records (district-level yield, area, nutrient and weather data by year) and the agricultural guidance documents (crop best-practice PDFs). No new data is ingested.
- The user interacts through the existing notebook/Python entry point; no new user interface is in scope.
- Answers are in English.
- The Judge's review and the Summarizer's final answer are automatic (performed by agents), not human approval steps.
- Change request 2026-10-05: the Judge reviews only the historical answers, and a new Summarizer writes the final answer (replacing the Judge as final approver).
- The existing Judge follow-up mechanism (asking an agent directly) is reused for follow-up requests and extended to reach the Farming Specialist.
- The Optimistic, Risk-Averse and Judge agents keep their existing roles, goals and backstories; the Farming Specialist and the Summarizer are new.
- Retiring the manager-led crew means it is no longer the entry point; its agent definitions may remain on disk.
- Maximum of 4 parts per question and 3 Judge follow-ups keeps runs bounded on the local model.
