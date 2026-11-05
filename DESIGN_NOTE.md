# Design note

**State and trade-off.** One Python agent uses Anthropic for request understanding and tool choice. LangGraph checkpoints only consequential actions: propose → interrupt for approval → execute → verify. SQLite separately stores conversation facts, preferences, mock provider state and an action ledger. The checkpoint restores the pause; the ledger owns side-effect truth. This extra state layer keeps safety independent of graph reruns. No additional agents or services are used.

**Approval and recovery.** A proposal freezes action type, user, provider, booking/hold ID, slot, displayed price and a stable idempotency key before any provider call. The code records what was shown and approves only an exact match; changed details require a new action. Repeated confirmations return the recorded outcome. After a booking timeout, `get_booking_status` resolves the same key before success is reported. If that lookup fails, the action stays pending; restart checks status before another provider call. The mock also deduplicates by key. Cancel retries its simulated 503 once; a reschedule conflict leaves the old slot intact.

**Memory and trust.** Preferences are scoped to a user and marked explicit or inferred. Only current, relevant, confirmed preferences enter the prompt. Forgotten and unconfirmed facts, plus old chat that might repeat them, are excluded. Selected memory is capped at 4 KB and the full model input is bounded. Reviews remain untrusted data. Code checks held slots, time, budget, booking ownership and approval. Model prose cannot claim booking success; the app renders verified outcomes.

**Evidence.** JSONL records transitions, model calls, token usage and estimated cost when available, tool arguments, sanitized results, latency, sent context and outcomes. `EVAL_TABLE.md` is generated from 34 offline simulated-user scenarios. Unit tests cover contracts and restart. `TIMEOUT_TRACE.txt` is the raw end-to-end trace; its decisive excerpt is:

| Event | Meaning |
|---|---|
| `approved` → `book_call` | Exact approval; one stable key sent. |
| `booking_timeout` | Acknowledgement lost. |
| `status_checked: confirmed` → `confirmed` | Same key verified before user-facing success. |

The mock recorded one booking and one booking call.
