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

Recorded 6 Nov 2026 with the live model (python -m errand.examples NAME PATH):
  example_reschedule_ac.json      "Move my AC servicing to Saturday." (fixture: one AC booking on 10 Nov)
  example_same_electrician.json   "Book the same electrician as last time, but not before 10 a.m." (fixture: electrician-1 booking on 20 Oct)
Their replays are the evals recorded_reschedule_ac and recorded_same_electrician. On replay, a recorded reschedule or cancel gets the current run's booking id.
