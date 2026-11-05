# Orange Errand Agent

Local, single-agent home-service booking demo for the Orange engineering assignment. It uses eight in-process mock provider tools and code-enforced approval. No real provider or payment integration exists.

## Run locally

Requires Python 3.11–3.13. From this directory:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e . pytest
.venv/bin/python -m pytest -q
.venv/bin/python -m errand.evals
```

`pytest` runs the unit tests and all 50 offline simulated-user scenarios, and fails if the committed `EVAL_TABLE.md` is stale. `python -m errand.evals` regenerates that table. Tests and evals need no API key.

## Live demo (chat left, trace right)

Place your own Anthropic key in the environment or in an untracked `.env` file containing `ANTHROPIC_API_KEY=...`. The key is read server-side and never sent to the page. The app reserves at most 20 live model calls per runtime database, limits each request's input size, caps each model output at 512 tokens, and retries a failed model call once. Use a dedicated Anthropic workspace with a small spend limit as the authoritative cost cap.

```sh
.venv/bin/python -m errand.web --scenario timeout_success
```

Open `http://127.0.0.1:8765` (loopback only; `--port` changes it, `--scripted` runs an offline interface check with no key).

1. Ask: "Find a plumber for my kitchen tap on 6 November in Indiranagar, mornings only, under ₹1,500."
2. Ask: "Read the reviews for plumber-1, then hold its 9am slot and book it." The trace shows the injected review flagged, and the app asks you to approve the exact action.
3. Click **Approve exact action**. The `timeout_success` scenario loses the booking acknowledgement after the provider booked; the trace shows `booking_timeout`, then `status_checked: confirmed`, then `confirmed`. One provider booking exists.
4. Click **Simulate provider cancellation**. The agent notices without a user message, finds a replacement within your known preferences, and asks for fresh approval. It never books a replacement on its own.

Each trace line expands to its raw JSON. Review text is untrusted and is rendered as plain text.

## Evidence

- [Design note](DESIGN_NOTE.md): state, approval, idempotency, memory and trace excerpt.
- [Generated eval table](EVAL_TABLE.md): 50 executed offline scenarios, each with the invariant it asserts.
- [Real bugs](BUGS.md): observed failures, causes, fixes and regression tests.
- [Live trace](TIMEOUT_TRACE_LIVE.txt): raw trace of a real-model run (Claude Sonnet 5.5) through timeout-after-success to verified booking.
- [Scripted trace](TIMEOUT_TRACE.txt): the same flow with the scripted model, kept as a deterministic reference.

## Reliability proof and recorded replay

- [FUZZ_REPORT.md](FUZZ_REPORT.md): 500 seeded random conversations with injected faults (timeouts after success, status errors, expired holds, changed proposals, repeated confirms, restarts, provider cancellations, injected reviews). 3,492 events, 0 invariant violations. Regenerate with `python -m errand.fuzz --runs 500 --seed 1`; tests prove the checker catches deliberately broken code.
- `cassettes/session.json` is a recorded real-Claude conversation. Replay it with no key: `python -m errand.web --replay cassettes/session.json`. Record a new one with `--record PATH` (needs a key).

## Contract and assumptions

Mock return shapes are JSON objects/lists: search returns 3 providers with `id`, `price`, `rating`, `slots`, `attributes`; `get_provider` returns details (`name`, `price`, `rating`, `attributes`) and `reviews`, with the injection on provider number 1 of each service; hold returns `hold_id`, `expires_at`, `price`; booking and mutation tools return outcome strings; status returns `confirmed` or `not_found`; list returns booking `id`, `provider`, `service`, `slot`, `status`. Named scenarios (or a seeded draw: 70% success, 15% slot taken, 15% timeout of which half booked) make outcomes reproducible. The model cannot create a provider booking without a held slot and a separate approval click. An expired hold produces a new hold and a new approval, never a silent rebook.

`estimated_usd` in the trace uses assumed rates of $2 per million input tokens and $10 per million output tokens. It is an estimate, not billing data.

Before sharing this folder, remove `.env` or share the repository with `git clone`/`git archive` only.

## No API key? Run it offline

`python -m errand.web --scripted --port 8766` runs the whole page with a scripted model and the same approval and booking code, so reviewers can try it without a key. `.env.example` shows the live-model setting. `pytest` and the evals never call the API.

## Known limits

- One demo user (`demo`); no sign-in. The provider is a local mock with fixed prices and slots.
- The page keeps its "pending approval" pointer in server memory and restores it from the database on reload; it is built for one browser tab.
- Live-model replies take about 10–20 seconds because the model makes several tool calls in turn.
- Model picks can still vary between runs; code checks every tool argument, so a wrong pick is blocked or asks again rather than booking.
- Cost figures are estimates from assumed per-token rates.
