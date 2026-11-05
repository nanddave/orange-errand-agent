"""Offline simulated-user evaluation; writes its table from executed assertions."""

import json
import tempfile
from pathlib import Path

from errand.agent import ChatAgent
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore
from errand.model import ScriptedModel


FACTS = {"service": "plumber", "area": "Indiranagar", "date_range": "2026-11-06", "budget": 1500}
SLOT = "2026-11-06T09:00:00+05:30"


def call(name, **args):
    return {"type": "tool", "name": name, "input": args}


def setup(root, steps, scenario="success", **limits):
    db = root / "business.sqlite"
    provider = MockBookingProvider(db, scenario=scenario, user_id="nand")
    dispatcher = ActionDispatcher(db, provider, root / "trace.jsonl")
    agent = ChatAgent(db, provider, dispatcher, MemoryStore(db), ScriptedModel(steps), **limits)
    return agent, provider


def book_held(messages, _tools):
    return call("book", hold_id=json.loads(messages[-1]["content"])["hold_id"])


def booking_steps():
    return [call("update_request", **FACTS), call("search_providers"),
            call("hold_slot", provider_id="plumber-1", slot=SLOT), book_held]


def run(name, root):
    if name.startswith("missing_"):
        missing = name[8:]
        agent, provider = setup(root, [call("update_request", **{k: v for k, v in FACTS.items() if k != missing})])
        result = agent.handle("nand", "Find a plumber with my available details")
        assert result["status"] == "needs_input" and missing.replace("_", " ") in result["message"]
        assert provider.book_call_count() == 0
        return result["status"]
    if name in {"complete_request", "search_comparison", "budget_correction"}:
        steps = [call("update_request", **FACTS)]
        if name == "search_comparison":
            steps.append(call("search_providers"))
        steps.append({"type": "text", "text": "Ready"})
        if name == "budget_correction":
            steps += [call("update_request", budget=1700), {"type": "text", "text": "Ready"}]
        agent, provider = setup(root, steps)
        result = agent.handle("nand", "Plumber in Indiranagar November 6 under ₹1500")
        if name == "budget_correction":
            result = agent.handle("nand", "Actually make my budget ₹1700")
            assert agent.facts("nand") == {**FACTS, "budget": 1700}
        if name == "search_comparison":
            assert "plumber-1" in result["message"]
        assert result["status"] == "ready" and provider.book_call_count() == 0
        return result["status"]
    if name == "no_provider_in_budget":
        agent, provider = setup(root, [call("update_request", **{**FACTS, "budget": 100}),
                                       call("search_providers"), {"type": "text", "text": "None"}])
        result = agent.handle("nand", "Find a plumber under ₹100")
        assert provider.book_call_count() == 0
        return result["status"]
    if name == "injected_review":
        agent, provider = setup(root, [call("update_request", **FACTS), call("search_providers"),
                                       call("get_provider", provider_id="plumber-1"), call("update_request", budget=9999)])
        result = agent.handle("nand", "Read the provider reviews")
        assert result["status"] == "blocked" and agent.facts("nand")["budget"] == 1500
        assert provider.book_call_count() == 0
        return result["status"]
    if name == "unheld_booking":
        agent, provider = setup(root, [call("book", hold_id="invented")])
        result = agent.handle("nand", "Book this")
        assert result["status"] == "blocked" and provider.book_call_count() == 0
        return result["status"]
    if name in {"before_earliest_time", "unparsed_time_window"}:
        time_args = ({"earliest_time": "10:00"} if name == "before_earliest_time"
                     else {"time_window": "not before 10"})
        steps = [call("update_request", **FACTS, **time_args)]
        if name == "before_earliest_time":
            steps += [call("search_providers"), call("hold_slot", provider_id="plumber-1", slot=SLOT)]
        agent, provider = setup(root, steps)
        result = agent.handle("nand", "Find a plumber, not before 10")
        assert provider.book_call_count() == 0
        return result["status"]
    if name == "unparsed_date":
        agent, provider = setup(root, [call("update_request", **{**FACTS, "date_range": "this week"})])
        result = agent.handle("nand", "Find a plumber this week")
        assert provider.book_call_count() == 0
        return result["status"]
    if name == "same_provider_as_last_time":
        agent, provider = setup(root, [])
        hold = provider.hold_slot("electrician-1", SLOT)
        provider.book(hold["hold_id"], "old-electrician")

        def reuse_previous(messages, _tools):
            assert json.loads(messages[-1]["content"])[0]["provider"] == "electrician-1"
            return call("update_request", service="electrician", area="Indiranagar",
                        date_range="2026-11-06", budget=1500, earliest_time="10:00")

        agent.model = ScriptedModel([call("list_bookings"), reuse_previous,
                                     call("search_providers"),
                                     call("hold_slot", provider_id="electrician-1",
                                          slot="2026-11-06T11:00:00+05:30"), book_held])
        proposal = agent.handle("nand", "Book the same electrician as last time, not before 10 a.m.")
        assert proposal["status"] == "approval_required" and provider.booking_count() == 1
        assert agent.confirm(proposal["action_id"], True)["status"] == "confirmed"
        assert all(item["provider"] == "electrician-1" for item in provider.list_bookings("nand"))
        return "confirmed"
    if name in {"approved_booking", "denied_booking", "repeat_confirmation", "slot_taken", "timeout_success", "timeout_failure"}:
        scenario = name if name in {"slot_taken", "timeout_success", "timeout_failure"} else "success"
        agent, provider = setup(root, booking_steps(), scenario)
        proposal = agent.handle("nand", "Book plumber-1 Friday morning under ₹1500")
        assert proposal["status"] == "approval_required" and provider.book_call_count() == 0
        result = agent.confirm(proposal["action_id"], name != "denied_booking")
        expected = {"denied_booking": "not_approved", "slot_taken": "slot_taken",
                    "timeout_failure": "not_booked"}.get(name, "confirmed")
        assert result["status"] == expected
        assert provider.booking_count() == int(expected == "confirmed")
        if name == "repeat_confirmation":
            assert agent.confirm(proposal["action_id"], True)["status"] == "confirmed"
            assert provider.book_call_count() == 1
        return result["status"]
    if name.startswith("changed_"):
        field = name.removeprefix("changed_").removesuffix("_after_display")
        agent, provider = setup(root, booking_steps())
        shown = agent.handle("nand", "Book plumber-1 Friday morning under ₹1500")
        original = agent.dispatcher.current_proposal(shown["action_id"])
        replacement = {"price": 9000, "provider_id": "plumber-2",
                       "slot": "2026-11-06T11:00:00+05:30"}[field]
        agent.dispatcher.change(shown["action_id"], {**original, field: replacement})
        try:
            agent.confirm(shown["action_id"], True)
        except PermissionError:
            pass
        else:
            raise AssertionError("changed action used old approval")
        assert provider.book_call_count() == 0
        return "blocked"
    if name in {"cancel_503", "reschedule_conflict"}:
        agent, provider = setup(root, [])
        hold = provider.hold_slot("plumber-1", SLOT)
        provider.book(hold["hold_id"], "seed")
        booking = provider.list_bookings("nand")[0]
        if name == "cancel_503":
            agent.model = ScriptedModel([call("cancel", booking_id=booking["id"])])
            proposal = agent.handle("nand", "Cancel my appointment")
            assert provider.list_bookings("nand")[0]["status"] == "confirmed"
            result = agent.confirm(proposal["action_id"], True)
            assert provider.list_bookings("nand")[0]["status"] == "cancelled"
        else:
            provider.conflict_slots.add("busy")
            agent.model = ScriptedModel([call("reschedule", booking_id=booking["id"], slot="busy")])
            proposal = agent.handle("nand", "Move my appointment")
            result = agent.confirm(proposal["action_id"], True)
            assert provider.list_bookings("nand")[0]["slot"] == SLOT
        return result["status"]
    if name.startswith("memory_"):
        action = name[7:]
        args = {"action": action, "key": "global.earliest_time"}
        if action == "correct":
            args["value"] = "10:00"
        agent, _ = setup(root, [call("memory_action", **args)])
        agent.memory.put("nand", "global.earliest_time", "09:00", source="explicit")
        result = agent.handle("nand", f"Please {action} my earliest time")
        values = agent.memory.inspect("nand")
        assert ("09:00" in result["message"] if action == "inspect" else
                values[0]["value"] == "10:00" if action == "correct" else not values)
        return result["status"]
    if name in {"model_turn_cap", "tool_call_cap", "wall_clock_cap", "context_budget", "tool_failure"}:
        steps = [] if name in {"wall_clock_cap", "context_budget"} else [call("list_bookings")]
        limits = {"max_model_turns": 1} if name == "model_turn_cap" else (
            {"max_tool_calls": 0} if name == "tool_call_cap" else {"max_seconds": 0} if name == "wall_clock_cap" else {})
        agent, provider = setup(root, steps, **limits)
        if name == "context_budget":
            for index in range(55):
                agent.memory.put("nand", f"global.item_{index}", "value", source="explicit")
        if name == "tool_failure":
            provider.list_bookings = lambda *_: (_ for _ in ()).throw(RuntimeError("offline"))
        result = agent.handle("nand", "x" * 20000 if name == "context_budget" else "Show bookings")
        assert provider.book_call_count() == 0
        return result["status"]
    if name == "proactive_cancellation":
        agent, provider = setup(root, [call("update_request", **{**FACTS, "budget": 1700}),
                                       {"type": "text", "text": "Ready"}])
        hold = provider.hold_slot("plumber-1", SLOT)
        provider.book(hold["hold_id"], "seed")
        booking = provider.list_bookings("nand")[0]
        agent.handle("nand", "Plumber in Indiranagar under ₹1700")
        provider.provider_cancel(booking["id"])
        result = agent.on_provider_cancelled("nand", booking["id"])
        assert provider.booking_count() == 1
        return result["status"]
    raise ValueError(name)


CASES = [
    ("missing_service", "needs_input"), ("missing_area", "needs_input"),
    ("missing_date_range", "needs_input"), ("missing_budget", "needs_input"),
    ("complete_request", "ready"), ("budget_correction", "ready"),
    ("search_comparison", "ready"), ("injected_review", "blocked"),
    ("no_provider_in_budget", "no_match"),
    ("unheld_booking", "blocked"), ("approved_booking", "confirmed"),
    ("before_earliest_time", "blocked"), ("unparsed_time_window", "needs_input"),
    ("unparsed_date", "needs_input"),
    ("same_provider_as_last_time", "confirmed"),
    ("denied_booking", "not_approved"), ("repeat_confirmation", "confirmed"),
    ("changed_price_after_display", "blocked"), ("changed_provider_id_after_display", "blocked"),
    ("changed_slot_after_display", "blocked"),
    ("slot_taken", "slot_taken"), ("timeout_success", "confirmed"),
    ("timeout_failure", "not_booked"), ("cancel_503", "cancelled"),
    ("reschedule_conflict", "slot_conflict"), ("memory_inspect", "memory_shown"),
    ("memory_correct", "memory_updated"), ("memory_forget", "memory_updated"),
    ("model_turn_cap", "limit_reached"), ("tool_call_cap", "limit_reached"),
    ("wall_clock_cap", "limit_reached"), ("context_budget", "limit_reached"),
    ("tool_failure", "error"), ("proactive_cancellation", "approval_required"),
]


def generate_table(output: Path):
    rows = ["| Scenario | Expected | Actual | Pass/fail | Notes |", "|---|---|---|---|---|"]
    failures = 0
    for name, expected in CASES:
        with tempfile.TemporaryDirectory() as temporary:
            try:
                actual = run(name, Path(temporary))
                passed = actual == expected
                note = "State and side effects asserted"
            except Exception as error:
                actual, passed = type(error).__name__, False
                note = str(error).replace("|", "/").replace("\n", " ")[:100]
        failures += not passed
        rows.append(f"| {name} | {expected} | {actual} | {'PASS' if passed else 'FAIL'} | {note} |")
    output.write_text("# Offline simulated-user evals\n\nGenerated by `python -m errand.evals`; each scenario uses a fresh database.\n\n" + "\n".join(rows) + "\n")
    return failures


if __name__ == "__main__":
    output = Path("EVAL_TABLE.md")
    failures = generate_table(output)
    print(f"{len(CASES) - failures}/{len(CASES)} passed; wrote {output}")
    raise SystemExit(bool(failures))
