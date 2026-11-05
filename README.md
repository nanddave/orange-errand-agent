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

The eval command rewrites `EVAL_TABLE.md` from 33 offline simulated-user scenarios. Tests and evals require no API key. `TIMEOUT_TRACE.txt` is a raw end-to-end scripted chat/booking trace showing timeout after success.

For the local chat and trace page, place your own Anthropic key in the environment or in an untracked `.env` file containing `ANTHROPIC_API_KEY=...`. The key is read server-side and never sent to the page. The app reserves at most 20 live model calls per runtime database, limits each request's input size, and caps each model output at 512 tokens. Use a dedicated Anthropic workspace with a small spend limit as the authoritative cost cap. Start the live model with:

```sh
.venv/bin/python -m errand.web --scenario timeout_success
```

Open `http://127.0.0.1:8765`. Ask for a plumber in Indiranagar on 6 November, morning, under ₹1,500; choose a provider and slot; approve the displayed exact action. The `timeout_success` scenario simulates a booking that succeeded despite a lost acknowledgement, so the trace should show a status lookup before success. Use `--scripted` for an offline interface check. The server binds only to loopback. To use another port, pass `--port 8766`.

The mock tool return shapes are JSON objects/lists: search returns 3 providers with `id`, `price`, `rating`, `slots`, `attributes`; provider details include `reviews`; hold returns `hold_id`, `expires_at`, `price`; booking and mutation tools return outcome strings; status returns `confirmed` or `not_found`; list returns booking `id`, `provider`, `service`, `slot`, `status`. Named scenarios make outcomes reproducible. The model cannot make a provider booking without a held slot and a separate approval click.

## Evidence

- [Design note](DESIGN_NOTE.md): state, approval, idempotency, memory and trace excerpt.
- [Generated eval table](EVAL_TABLE.md): 33 executed offline scenarios.
- [Real bugs](BUGS.md): observed failures, causes, fixes and regression tests.
- [Raw timeout trace](TIMEOUT_TRACE.txt): one scripted chat through verified booking.
- [Project plan](PID.md): original requirements and acceptance checklist.

For the 3–5 minute recording, show the page with chat left and trace right. Narrate the proposal, approval, timeout, authoritative status check and verified success. The recording URL and the AI-assistant transcript must be attached to the submission separately.
