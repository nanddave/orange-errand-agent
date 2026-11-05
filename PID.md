# Orange Errand Agent — project initiation document

**Status:** Plan amended 5 November 2026; first build gate passed locally on 5 November. No submission has begun.
**Source of truth:** [Orange_Engineering_Assignment.pdf](/Users/nanddave/Downloads/Orange_Engineering_Assignment.pdf), three pages. The hiring email gives a 7 November, 11:30 AM deadline without a timezone.
**Objective:** Build a locally runnable, chat-based home-service agent that uses a real LLM, mock providers, explicit approval, and verified recovery from uncertain tool outcomes. Supply the exact evidence requested in the PDF.

## Decision: one LangGraph agent, with code-owned action safety

Use a small LangGraph `StateGraph` in Python, with a durable SQLite checkpointer and synchronous checkpoint durability at action boundaries. LangGraph only sequences conversational steps and pauses/resumes; it does not own business truth. A standalone dispatcher, SQLite action ledger, and memory table own approval, stable action identity, idempotency, tool-call permission, outcome verification, and preferences. Their interfaces stay independent of LangGraph so the sequencer can be replaced without rewriting safety rules. Use a scripted model for offline adversarial evals, a few recorded real-model cassettes for realistic language behavior, and a real LLM for the live run. This is a proposed architecture, pending a narrow proof in the first build gate.

Why LangGraph fits: the PDF explicitly calls for an explicit workflow, human approval, persistent state across restarts, and observable transitions. Its Graph API, interrupts, and checkpointer directly support those needs. Why it is insufficient alone: nodes may rerun after an interrupt, retry, or crash; a checkpoint does not make a provider side effect exactly once. The stable key must be durable before the booking call, and timeout must lead to `get_booking_status`. Use no additional agents, LangSmith service, Postgres, RAG, or real provider integrations for this local assignment.

**First gate (4-hour maximum):** demonstrate approval pause/resume, timeout-after-success followed by a process restart, and a readable end-to-end trace. Pass only if (1) exactly one provider-side booking exists, (2) a resumed node makes no second provider booking call, and (3) the trace shows approval, action identity, timeout, status lookup, and verified final message in order. Failure of any criterion means switch the sequencer to plain Python immediately. Do not build both implementations. Pin `langgraph==1.2.19` and `langgraph-checkpoint-sqlite==3.1.3`; verify the `durability="sync"` call on these installed versions during the gate. These are planning pins, subject to an install and API check.

**Gate result:** Installed both pinned versions with Python 3.11; `graph.invoke(..., durability="sync")` worked. `tests/test_gate.py` passed: unapproved/changed-price actions made zero provider calls; the timeout-after-success/restart path produced one booking and one `book` call; the JSONL trace has ordered approval, key, timeout, recovery, status, and confirmed-message events. This is a narrow proof, not completion of the assignment's full tool, memory, model, or trace requirements.

## Boundaries and state

- One user-facing conversation thread is keyed by `user_id` and `thread_id`; preferences are scoped to `user_id` across threads.
- Structured workflow state: intent (`book`, `cancel`, `reschedule`, `inspect/correct/forget memory`), supplied facts, missing facts, candidate/hold, proposed action, approval status, current action identity, verified outcome, and counters. Store raw facts; assemble prompts from selected context each turn.
- A proposed consequential action contains action type, booking or hold identity, provider, slot, quoted price where applicable, and user-facing summary. The user confirms that exact proposal. Application code checks approval against the current proposal immediately before `book`, `cancel`, or `reschedule`; a changed provider, slot, or price voids it. A new hold after expiry is presented again before booking.
- For `reschedule`, bind approval to the existing booking ID, the new slot, and the displayed price, with no invented hold step. A conflict leaves the old booking intact.
- Persist one stable action ID and idempotency key before a side effect. A repeated confirmation or resumed node reuses that identity. An unknown booking result enters `verify`, which calls `get_booking_status` by hold ID or key before any retry or success claim. Do not attach an automatic graph retry to side-effect nodes.
- Provider reviews and all free text from tools are untrusted data. They can inform a provider summary but cannot change intent, approval, or tool permissions. Only structured, validated arguments reach the action dispatcher.
- Explicit memories can be used when current and relevant; inferred memories require confirmation before entering model context. Inspection, correction, and forgetting update the durable store. Build each prompt from current permitted facts under a fixed token cap and log the exact selected context and count.
- A simulated cancellation event is a separate entry point, not a fake user message. It verifies the affected booking, notifies the user, searches within confirmed preferences, then requests fresh approval before another booking.
- Hard caps cover model turns, tool calls, and wall-clock time per run. Every terminal or paused path returns a clear user-facing status. Use an injected clock for hold expiry and date expressions in deterministic tests.

## Exact mock-tool contract from PDF, page 1

| Tool | Required behavior to preserve |
|---|---|
| `search_providers(service, area, date_range)` | 3–8 providers, IDs, prices, ratings, slots, relevant attributes |
| `get_provider(provider_id)` | Details and free-text reviews; at least one injection attempt |
| `hold_slot(provider_id, slot)` | 10-minute hold, ID and expiry; configurable quote change |
| `book(hold_id, idempotency_key)` | 70% success, 15% slot taken, 15% timeout after possible success; half of timeouts did book; expired hold returns `hold_expired` |
| `get_booking_status(hold_id or idempotency_key)` | Authoritative booking outcome |
| `cancel(booking_id, idempotency_key)` | First call transient 503; retry succeeds |
| `reschedule(booking_id, slot, idempotency_key)` | Configurable `slot_conflict` |
| `list_bookings(user_id)` | Past/upcoming booking ID, provider, service, slot, status |

Seeded randomness or named scenarios must make every outcome reproducible. The exact return shapes not specified in the PDF should be documented in the README and kept consistent across mocks and tests.

## Requirement-to-proof checklist

| PDF requirement | Proof before submission |
|---|---|
| Missing details and corrections | Simulated user preserves unchanged fields and asks only for missing ones |
| Approval for book/cancel/reschedule | Dispatcher rejects absent, stale, or mismatched approval; trace records reason |
| Bounded execution and errors | Caps and exhausted retries return user-facing outcomes without hanging |
| No duplicate bookings across retries/restarts | Durable key plus status reconciliation; count provider-side bookings |
| Prompt-injected review | Malicious review cannot alter action, permissions, or user intent |
| Persistent, editable memory | New thread recalls explicit preference; correct/forget removes old fact |
| Trace per run | JSONL or equivalent: transitions, model calls, tools/args, sanitized results, latency, tokens and estimated cost when available |
| At least 20 offline automated scenarios | Scripted model, deterministic mocks, generated expected/actual/pass/fail/notes table |
| Proactive cancellation | Event triggers notice and replacement search without user prompt; fresh approval required |
| Context management | 50+ memories plus long chat; fixed token cap; each turn logs sent context; forgotten/unconfirmed facts absent |
| Real LLM | Live run shows model understanding requests and driving tool choices; tests remain offline |
| Deliverables | Repo, README, one-page design note, generated eval table, three genuine bugs with regression tests, raw timeout-after-success trace and annotated excerpt, 3–5 minute demo with chat/trace side by side, AI transcript |

## Evaluation inventory (tests assert state and tool effects, not phrasing)

1. All booking details in first message: no redundant question.
2. Missing area only: ask for area only.
3. Budget correction preserves service, area, and window.
4. “Same electrician” resolves via `list_bookings`.
5. Unapproved booking is blocked.
6. Unapproved cancellation is blocked.
7. Unapproved reschedule is blocked.
8. Changed provider after approval requires approval again.
9. Changed slot after approval requires approval again.
10. Changed quoted price after approval requires approval again.
11. Expired hold leads to new hold and approval.
12. Slot taken reports failure/alternative without a success claim.
13. Timeout after success is resolved by status, with one booking.
14. Timeout without success is resolved by status, without a success claim.
15. Repeated “yes” creates one booking.
16. Process restart before/after book call preserves the action identity.
17. Cancel 503 is retried with the same key and succeeds once.
18. Reschedule conflict leaves original booking intact.
19. Prompt-injected review cannot override user intent or approval.
20. Unexpected tool exception returns a clear outcome.
21. Model-turn cap returns a clear outcome.
22. Tool-call cap returns a clear outcome.
23. Wall-clock cap returns a clear outcome.
24. Memory inspection, correction, and forgetting survive a new thread.
25. Fifty memories plus long chat stay within context cap; forgotten and unconfirmed inferred facts are absent.
26. Provider cancellation event produces notice and replacement search; no new booking without fresh approval.
27. Recorded real-model cassette handles one natural booking request and one correction without changing the code-owned invariants.

## Build gates and acceptance

1. **Safety gate, 4 hours maximum:** build only the mock booking/status path, standalone dispatcher/ledger, a minimal LangGraph sequencer, approval pause/resume, and trace. Run the three pass/fail criteria above. Start `BUGS.md` now and log actual failures as they occur.
2. **Contract gate:** complete mock fixtures and an executable contract check for all eight tools, seeded outcomes, and injected clock. Confirm the PDF has no unmatched tool behavior.
3. **Behavior gate:** cancel, reschedule, memory, context filtering, proactive event, limits, and prompt-injection defense with offline scenarios.
4. **Evidence gate:** at least 20 passing scenario rows generated by tests, trace verified against tool-side state, three *actual* development bugs logged with cause/fix/test, one-page design note, real-LLM run, demo, transcript, and clean-run README.
5. **Submission gate:** verify every deliverable opens and the demo matches the repo; submit before the email deadline. Assume 11:30 AM India Standard Time (10:00 AM Dubai) until clarified and aim to submit on the evening of 6 November Dubai time.

**Schedule and cut order:** 5 November evening: safety gate and architecture decision. Overnight/6 November morning: remaining mock contracts, conversation actions, memory, proactive event. 6 November afternoon: at least 20 offline evals, real-model cassettes, trace and bug documentation. 6 November evening: design note, README, demo, transcript, clean-run verification and submission. If behind, cut the local web page first and record two terminal panes, then cut presentation polish. Never cut approval, idempotency, authoritative timeout check, 20 evals, generated table, three genuine bugs, trace, demo, or transcript. If three genuine bugs have not occurred, disclose the shortfall rather than inventing them.

## Questions to settle before implementation

- Anthropic is the preferred live provider; the user has a key. Read it from `ANTHROPIC_API_KEY`, never commit it or ask for it in chat. The reviewer may supply their own key. Set hard model-turn/token limits in the app; recommend a dedicated Anthropic workspace with a small monthly spend cap in the Console for the user's key. Offline scenarios need no key.
- Which `user_id`/thread identity should the demo use? Prefer a small local page with chat left and trace right after the safety gate; use two terminal panes if the schedule slips. Neither interface may receive or display the API key.
- How should the mock represent cancellation events and ambiguous `get_booking_status` results? Define scenarios without altering the PDF's stated tool inputs, outputs, or failures.
- Does “PID” here mean this project initiation document, or did the user intend a PRD? Keep this document as the planning reference either way.

## Prompt for a focused independent analysis before coding

> Read `/Users/nanddave/Downloads/Orange_Engineering_Assignment.pdf` in full. Treat its tool contracts and deliverables as the source of truth; do not infer missing requirements from prior summaries. Produce (1) a requirement-to-test matrix with PDF page references, (2) the smallest single-agent architecture comparing LangGraph `StateGraph` with plain Python on restart recovery, approval, side-effect idempotency, context control, and reviewer clarity, (3) a state diagram and exact approval/action invariants, (4) at least 20 deterministic offline eval scenarios, and (5) a list of unresolved contract details that must be documented rather than invented. Prefer LangGraph only if a narrow timeout-after-success and restart proof validates it. Do not write implementation code, add agents, invent bug reports, or claim that checkpointing makes external side effects exactly once. Keep the final one-page design note separate from this planning analysis.

## Sources for the framework decision

- [LangGraph persistence: checkpointers versus stores](https://docs.langchain.com/oss/python/langgraph/persistence)
- [LangGraph interrupts and rerun behavior](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [LangGraph fault tolerance and idempotency](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)
- [LangGraph checkpoint durability modes](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [SQLite checkpointer for lightweight local use](https://reference.langchain.com/python/langgraph.checkpoint.sqlite/SqliteSaver)
