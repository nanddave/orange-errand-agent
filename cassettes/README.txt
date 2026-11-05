Cassettes: recorded model conversations for offline replay.

Format: a JSON list of model "steps", in order, exactly as ChatAgent consumes them:
  {"type": "tool", "name", "input", "tool_use_id", "assistant_content", "usage"}
  {"type": "text", "text", "usage"}
Only these step dicts are stored; never the API key or request headers.

Record (live, needs ANTHROPIC_API_KEY):
  .venv/bin/python -m errand.web --record cassettes/session.json
Replay (offline, no key):
  .venv/bin/python -m errand.web --replay cassettes/session.json

On replay, a recorded `book` call gets the current run's hold_id (ids change per run).
When the cassette runs out the agent returns a friendly error and books nothing.
