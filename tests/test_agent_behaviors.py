import json

from errand.agent import ChatAgent
from errand.evals import booking_steps
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore
from errand.model import ScriptedModel


def make(tmp_path, steps, clock=None, **provider_args):
    db = tmp_path / "business.sqlite"
    provider = MockBookingProvider(db, scenario="success", user_id="nand", **({"clock": clock} if clock else {}), **provider_args)
    dispatcher = ActionDispatcher(db, provider, tmp_path / "trace.jsonl")
    return ChatAgent(db, provider, dispatcher, MemoryStore(db), ScriptedModel(steps)), provider


def mem(action, **args):
    return {"type": "tool", "name": "memory_action", "input": {"action": action, **args}}


def context(chat):
    return chat.memory.build_context("nand", "plumber", "", max_bytes=500)


def trace(chat):
    return [json.loads(line) for line in chat.dispatcher.trace.read_text().splitlines()]


def booked(chat):
    shown = chat.handle("nand", "Book a plumber Friday morning under ₹1500")
    assert shown["status"] == "approval_required"
    return shown


def test_remember_is_explicit_and_used_by_new_agent_instance(tmp_path):
    chat, _ = make(tmp_path, [mem("remember", key="global.earliest_time", value="10:00")])
    assert chat.handle("nand", "Remember I prefer after 10")["status"] == "memory_updated"
    assert chat.memory.inspect("nand")[0]["source"] == "explicit"
    other, _ = make(tmp_path, [])
    assert "earliest_time: 10:00" in context(other)


def test_remember_with_bad_key_needs_input(tmp_path):
    chat, _ = make(tmp_path, [mem("remember", key="earliest", value="10:00")])
    assert chat.handle("nand", "Remember that")["status"] == "needs_input"
    assert chat.memory.inspect("nand") == []


def test_confirm_memory_puts_inferred_value_in_context_only_after_confirmation(tmp_path):
    chat, _ = make(tmp_path, [mem("confirm", key="global.morning"), mem("confirm", key="global.nope")])
    chat.memory.put("nand", "global.morning", "yes", source="inferred")
    assert "morning" not in context(chat)
    assert chat.handle("nand", "Yes, that is right")["status"] == "memory_updated"
    assert "morning: yes" in context(chat)
    assert chat.handle("nand", "Confirm nope")["status"] == "not_found"


def test_correct_unknown_key_is_not_found_without_error(tmp_path):
    chat, _ = make(tmp_path, [mem("correct", key="global.nothing", value="x")])
    result = chat.handle("nand", "Change it")
    assert result["status"] == "not_found"
    assert result["message"] == "No saved preference with that name."


def test_confirmed_booking_infers_unconfirmed_area_hidden_from_context(tmp_path):
    chat, provider = make(tmp_path, booking_steps())
    shown = booked(chat)
    assert chat.confirm(shown["action_id"], True)["message"] == "Booking confirmed."
    row = chat.memory.inspect("nand")[0]
    assert (row["key"], row["value"], row["source"], row["confirmed"]) == ("global.preferred_area", "Indiranagar", "inferred", 0)
    assert "preferred_area" not in context(chat)
    assert any(e["event"] == "memory_inferred" and e["key"] == "global.preferred_area" for e in trace(chat))


def test_inference_does_not_overwrite_explicit_or_resurrect_forgotten(tmp_path):
    chat, _ = make(tmp_path, booking_steps())
    chat.memory.put("nand", "global.preferred_area", "Koramangala", source="explicit")
    assert chat.confirm(booked(chat)["action_id"], True)["status"] == "confirmed"
    assert chat.memory.inspect("nand")[0]["value"] == "Koramangala"

    (tmp_path / "x").mkdir()
    other, _ = make(tmp_path / "x", booking_steps())
    other.memory.put("nand", "global.preferred_area", "Old", source="explicit")
    other.memory.forget("nand", "global.preferred_area")
    assert other.confirm(booked(other)["action_id"], True)["status"] == "confirmed"
    assert other.memory.inspect("nand") == []


def test_expired_hold_reproposes_new_action_needing_fresh_approval(tmp_path):
    now = [0.0]
    chat, provider = make(tmp_path, booking_steps(), clock=lambda: now[0])
    shown = booked(chat)
    now[0] = 1000.0
    provider.quote_delta = 50
    again = chat.confirm(shown["action_id"], True)
    assert again["status"] == "approval_required" and again["action_id"] != shown["action_id"]
    assert "₹1450" in again["message"] and "was ₹1400" in again["message"]
    assert provider.booking_count() == 0
    assert chat.dispatcher.status(shown["action_id"]) == "hold_expired"
    events = [e["event"] for e in trace(chat)]
    assert "hold_reproposed" in events
    assert chat.confirm(again["action_id"], True)["status"] == "confirmed"
    assert provider.booking_count() == 1
    with __import__("sqlite3").connect(chat.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM provider_bookings").fetchone()[0] == 1


def test_expired_hold_with_over_budget_new_quote_is_blocked(tmp_path):
    now = [0.0]
    chat, provider = make(tmp_path, booking_steps(), clock=lambda: now[0])
    shown = booked(chat)
    now[0] = 1000.0
    provider.quote_delta = 500
    result = chat.confirm(shown["action_id"], True)
    assert result == {"status": "blocked", "message": "The new quote exceeds your budget. No booking was made."}
    assert provider.booking_count() == 0


def test_get_provider_flags_injected_review_in_trace(tmp_path):
    chat, _ = make(tmp_path, [
        {"type": "tool", "name": "update_request", "input": {
            "service": "plumber", "area": "Indiranagar", "date_range": "2026-11-06", "budget": 1500}},
        {"type": "tool", "name": "search_providers", "input": {}},
        {"type": "tool", "name": "get_provider", "input": {"provider_id": "plumber-1"}},
        {"type": "text", "text": "done"},
    ])
    chat.handle("nand", "Plumber in Indiranagar under 1500")
    events = trace(chat)
    flagged = [e for e in events if e["event"] == "review_flagged"]
    assert flagged and flagged[0]["provider_id"] == "plumber-1" and "budget" in flagged[0]["excerpt"]
    result = next(e for e in events if e["event"] == "tool_result" and e["tool"] == "get_provider")
    assert all(len(r) <= 80 for r in result["result"]["reviews_untrusted"])


def test_abbreviated_model_slot_is_canonicalised_but_unoffered_slot_is_blocked(tmp_path):
    from errand.evals import FACTS, SLOT, book_held, call, setup

    agent, provider = setup(tmp_path, [call("update_request", **FACTS), call("search_providers"),
                                       call("hold_slot", provider_id="plumber-1", slot=SLOT[:16]), book_held])
    result = agent.handle("nand", "Book plumber-1 in the morning")
    assert result["status"] == "approval_required"
    assert agent.dispatcher.current_proposal(result["action_id"])["slot"] == SLOT

    other = tmp_path / "other"
    other.mkdir()
    agent, provider = setup(other, [call("update_request", **FACTS), call("search_providers"),
                                    call("hold_slot", provider_id="plumber-1", slot="2026-11-06T10:00")])
    assert agent.handle("nand", "Book plumber-1")["status"] == "blocked"
    assert provider.book_call_count() == 0


def test_space_separated_model_slot_is_canonicalised(tmp_path):
    from errand.evals import FACTS, SLOT, book_held, call, setup

    agent, _ = setup(tmp_path, [call("update_request", **FACTS), call("search_providers"),
                                call("hold_slot", provider_id="plumber-1", slot="2026-11-06 09:00"), book_held])
    result = agent.handle("nand", "Book plumber-1 in the morning")
    assert result["status"] == "approval_required"
    assert agent.dispatcher.current_proposal(result["action_id"])["slot"] == SLOT
