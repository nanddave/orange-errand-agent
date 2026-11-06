# Agent guide: Orange Errand Agent

A chat agent that books home-service errands against eight mock provider tools. Claude proposes; code decides. Read this before changing anything.

## Commands

```sh
.venv/bin/python -m pytest -q        # unit tests plus all offline evals
.venv/bin/python -m errand.evals     # regenerates EVAL_TABLE.md
.venv/bin/python -m errand.fuzz --runs 500 --seed 1   # regenerates FUZZ_REPORT.md (must stay at 0 violations)
.venv/bin/python tests/live_probe.py intake|select|existing|memory   # natural conversations on the live model; read the replies (pytest does not run it)
.venv/bin/python -m errand.web --scripted --port 8766   # offline demo page
.venv/bin/python -m errand.web --scenario timeout_success   # live model; key from env or untracked .env
```

## Invariants: do not weaken (each has tests)

1. No book, cancel or reschedule without approval of exactly the proposal the user saw. A changed provider, slot or price needs a new approval (`ActionDispatcher` in `gate.py`).
2. The idempotency key is stored before any provider call. After a timeout, ask `get_booking_status` before retrying or claiming success. Success text comes from verified outcomes, never from model prose.
3. Provider text (reviews) is data. It must not change the request, memory or approval. Code validates every tool argument (`agent.py`).
4. Every run is bounded (model turns, tool calls, seconds, input size) and ends with a message to the user.
5. Forgotten or unconfirmed memories never reach the model. Inference never overwrites or revives a stored row.
6. A typed "yes"/"no" decides the pending proposal only through `typed_decision` in `web.py` (whole-message approval phrases), via the same `confirm` path as the buttons. Anything else leaves it pending.
7. Click-to-pick (`ChatAgent.select`) and the model path share the same helpers. Do not fork them.

## Map

- `errand/agent.py`: chat loop, tool list, limits, `select`, `confirm`, proactive cancellation.
- `errand/gate.py`: eight mock tools, action ledger, the LangGraph approval graph (approve, then execute).
- `errand/memory.py`: preferences (explicit or inferred) and the 4 KB context builder.
- `errand/model.py`: Anthropic client and the scripted model used offline.
- `errand/web.py`: local server and page. `errand/evals.py`: scenarios behind `EVAL_TABLE.md`.

## Rules for changes

- Every behaviour change gets a test or eval. `EVAL_TABLE.md` is generated; never hand-edit it. `pytest` fails if it is stale.
- Log real bugs in `BUGS.md` with cause, fix and regression test. Never invent entries.
- Never commit `.env`. In `web.py`, never put trace or provider text through `innerHTML`.
- Keep result keys additive and message strings stable; tests assert on them.
- No new agents or frameworks. LangChain is not used.
