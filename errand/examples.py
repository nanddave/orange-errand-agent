"""The brief's two other example requests, run with a fixture history. Used to record real-model cassettes and to replay them offline."""

import sys
import tempfile
from pathlib import Path

from errand.agent import ChatAgent
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore

# Saturday is 2026-11-07. Each example is (seeded past booking, user turns). Turns after the first answer the agent's questions.
EXAMPLES = {
    "reschedule_ac": {
        "seed": ("ac", 1, "2026-11-10T09:00:00+05:30"),
        "turns": ["Move my AC servicing to Saturday."],
        "followups": ["Saturday 7 November at 9:00 AM is fine."],
    },
    "same_electrician": {
        "seed": ("electrician", 1, "2026-10-20T11:00:00+05:30"),
        "turns": ["Book the same electrician as last time, but not before 10 a.m."],
        "followups": ["Koramangala, 7 November, budget ₹2,000."],
    },
}


def build(root: Path, name: str, model):
    db = root / "business.sqlite"
    provider = MockBookingProvider(db, scenario="success", user_id="nand")
    service, number, slot = EXAMPLES[name]["seed"]
    provider.book(provider.hold_slot(f"{service}-{number}", slot)["hold_id"], f"seed:{name}")
    dispatcher = ActionDispatcher(db, provider, root / "trace.jsonl")
    return ChatAgent(db, provider, dispatcher, MemoryStore(db), model), provider


def run(root: Path, name: str, model):
    """Drive the example: first request, then follow-ups while the agent still needs input, then approve once if asked."""
    agent, provider = build(root, name, model)
    spec = EXAMPLES[name]
    results = [agent.handle("nand", spec["turns"][0])]
    for text in spec["followups"]:
        if results[-1]["status"] in {"needs_input", "ready"}:
            results.append(agent.handle("nand", text))
    if results[-1]["status"] == "approval_required":
        results.append(agent.confirm(results[-1]["action_id"], True))
    return agent, provider, results


if __name__ == "__main__":
    from errand.model import AnthropicModel, RecordingModel
    from errand.web import load_local_key

    load_local_key(Path(__file__).resolve().parent.parent / ".env")
    name, out = sys.argv[1], Path(sys.argv[2])
    with tempfile.TemporaryDirectory() as tmp:
        _, provider, results = run(Path(tmp), name, RecordingModel(AnthropicModel(), out))
        for item in results:
            print(item["status"], "|", item["message"][:200])
        print("bookings:", [(b["provider"], b["slot"], b["status"]) for b in provider.list_bookings("nand")], "book calls:", provider.book_call_count())
