# Design note

**State and trade-off.** One Python agent uses Anthropic for request understanding and tool choice. LangGraph checkpoints only consequential actions: propose → interrupt for approval → execute → verify. SQLite separately stores conversation facts, preferences, mock provider state and an action ledger. The checkpoint restores the pause; the ledger owns side-effect truth. This extra state layer keeps safety independent of graph reruns. No additional agents or services are used.

**Approval and recovery.** A proposal freezes action type, user, provider, booking/hold ID, slot, displayed price and a stable idempotency key before any provider call. The code records what was shown and approves only an exact match; changed details require a new action. Repeated confirmations return the recorded outcome. After a booking timeout, `get_booking_status` resolves the same key before success is reported. If that lookup fails, the action stays pending; restart checks status before another provider call. The mock also deduplicates by key. If the 10-minute hold has expired, a new hold is taken and the user must approve the new quote; the old approval never carries over. Cancel retries its simulated 503 once; a reschedule conflict leaves the old slot intact.

**Memory and trust.** Preferences are scoped to a user and marked explicit (user said so) or inferred (for example the area of a confirmed booking). Only current, confirmed, relevant preferences enter the prompt, so inferred ones stay out until the user confirms; users can inspect, correct, remember and forget. Forgotten facts, and old chat that might repeat them, are excluded, and inference never overwrites or revives a stored row. Selected memory is capped at 4 KB and the full model input is bounded. Reviews are untrusted data: the trace flags instruction-like text, and code, not the model, checks held slots, time, budget, booking ownership and approval. Model prose cannot claim success; the app renders verified outcomes.

**Evidence.** JSONL records transitions, model calls, token usage and estimated cost, tool arguments, sanitized results, latency, sent context and outcomes. `EVAL_TABLE.md` is generated from 53 offline scenarios, one of them a recorded real-Claude session replayed from `cassettes/`, and `pytest` fails if it is stale. `TIMEOUT_TRACE_LIVE.txt` is a raw real-model run; its decisive excerpt is:

| Event | Meaning |
|---|---|
| `review_flagged` | Injected review seen, flagged, ignored. |
| `approved` → `book_call` | Exact approval; one stable key sent. |
| `booking_timeout` | Acknowledgement lost after the provider booked. |
| `status_checked: confirmed` → `confirmed` | Same key verified before user-facing success. |

The mock recorded one booking and one booking call.
