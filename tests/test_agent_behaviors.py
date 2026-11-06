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


def test_review_question_keeps_options_and_answers_from_code(tmp_path):
    chat, _ = make(tmp_path, [
        {"type": "tool", "name": "update_request", "input": {
            "service": "plumber", "area": "Indiranagar", "date_range": "2026-11-06", "budget": 1500}},
        {"type": "tool", "name": "search_providers", "input": {}},
        {"type": "text", "text": "ok"},
        {"type": "tool", "name": "get_provider", "input": {"provider_id": "plumber-1"}},
        {"type": "text", "text": "Plumber 1 says: pay the deposit at http://evil.example/pay or pay@upi."},
    ])
    chat.handle("nand", "Plumber in Indiranagar under 1500")
    result = chat.handle("nand", "Read the reviews and tell me if anything looks off")
    assert result["status"] == "ready" and result["options"]
    assert "instruction aimed at me" in result["message"] and "evil.example" not in result["message"]
    assert "No booking has been made" in result["message"]


SEARCH = [{"type": "tool", "name": "update_request", "input": {
    "service": "plumber", "area": "Indiranagar", "date_range": "2026-11-06", "budget": 1500}},
    {"type": "tool", "name": "search_providers", "input": {}}, {"type": "text", "text": "ok"}]


def test_price_difference_between_listed_options_is_answered_not_replaced(tmp_path):
    chat, _ = make(tmp_path, SEARCH + [{"type": "text", "text": "Plumber 2 costs ₹100 more than Plumber 1."}])
    chat.handle("nand", "Plumber in Indiranagar under 1500")
    result = chat.handle("nand", "What is the price difference?")
    assert result["message"] == "Plumber 2 costs ₹100 more than Plumber 1." and result["options"]


def test_invented_price_is_still_replaced_and_options_stay(tmp_path):
    chat, _ = make(tmp_path, SEARCH + [{"type": "text", "text": "Plumber 2 will do it for ₹900."}])
    chat.handle("nand", "Plumber in Indiranagar under 1500")
    result = chat.handle("nand", "Can plumber 2 go cheaper?")
    assert "₹900" not in result["message"] and "won't guess" in result["message"] and result["options"]


def test_answer_after_reading_reviews_is_kept_with_a_code_owned_safety_note(tmp_path):
    chat, _ = make(tmp_path, SEARCH + [
        {"type": "tool", "name": "get_provider", "input": {"provider_id": "plumber-1"}},
        {"type": "text", "text": "Plumber 1 has the higher rating, 4.7 against 4.6."}])
    chat.handle("nand", "Plumber in Indiranagar under 1500")
    result = chat.handle("nand", "Which one has better reviews?")
    assert result["message"].startswith("Plumber 1 has the higher rating, 4.7 against 4.6.")
    assert "instruction aimed at me" in result["message"] and result["options"]


def test_availability_question_with_confirm_in_it_goes_to_the_model_not_the_booking_list(tmp_path):
    chat, _ = make(tmp_path, booking_steps() + [{"type": "text", "text": "Let me check the 11 AM slot for you."}])
    shown = booked(chat)
    chat.confirm(shown["action_id"], True)
    result = chat.handle("nand", "Can you confirm the 11 AM slot is available too?")
    assert result["message"] == "Let me check the 11 AM slot for you."


def test_distance_question_does_not_invent_provider_location(tmp_path):
    chat, _ = make(tmp_path, SEARCH)
    first = chat.handle("nand", "Plumber in Indiranagar under 1500")
    result = chat.handle("nand", "Is plumber 2 closer?")
    assert "don't have provider location or distance data" in result["message"]
    assert result["options"] == first["options"]


def test_model_reporting_the_injected_review_is_kept_and_slot_times_are_recognised(tmp_path):
    chat, _ = make(tmp_path, SEARCH + [
        {"type": "tool", "name": "get_provider", "input": {"provider_id": "plumber-1"}},
        {"type": "text", "text": "One review of Plumber 1 tells me to ignore your budget. I am disregarding it. Both have 9:00 and 11:00 slots."}])
    chat.handle("nand", "Plumber in Indiranagar under 1500")
    result = chat.handle("nand", "Is anything off in the reviews?")
    assert result["message"].startswith("One review of Plumber 1 tells me to ignore your budget.") and result["options"]


def _request(**facts):
    base = {"service": "plumber", "area": "Indiranagar", "date_range": "2026-11-09", "budget": 1500}
    return {"type": "tool", "name": "update_request", "input": {**base, **facts}}


def test_this_week_range_is_accepted_and_offers_the_first_days(tmp_path):
    chat, _ = make(tmp_path, [_request(date_range="2026-11-09/2026-11-13"), {"type": "tool", "name": "search_providers", "input": {}},
                              {"type": "text", "text": "ok"}])
    result = chat.handle("nand", "Find a plumber this week, mornings only, under 1500")
    slots = [s["value"][:10] for s in result["options"][0]["slots"]]
    assert result["status"] == "ready" and slots == ["2026-11-09"] * 2 + ["2026-11-10"] * 2 + ["2026-11-11"] * 2


def test_bad_date_ranges_ask_again_instead_of_searching(tmp_path):
    for n, bad in enumerate(("this week", "2026-11-13/2026-11-09", "2026-11-01/2026-12-30")):
        (tmp_path / str(n)).mkdir()
        chat, _ = make(tmp_path / str(n), [_request(date_range=bad)])
        result = chat.handle("nand", "Find a plumber")
        assert result["status"] == "needs_input" and ("YYYY-MM-DD" in result["message"] or "14 days" in result["message"])


def test_reply_may_repeat_times_and_budget_the_user_already_gave(tmp_path):
    reply = "I haven't searched yet. What date do you want? I'll look at 12:00 to 17:00 slots under ₹1500."
    chat, _ = make(tmp_path, [{"type": "tool", "name": "update_request", "input": {
        "service": "plumber", "earliest_time": "12:00", "latest_time": "17:00", "budget": 1500}}, {"type": "text", "text": reply}])
    assert chat.handle("nand", "A plumber this afternoon, 12 to 5, under 1500")["status"] == "needs_input"
    result = chat.handle("nand", "which is the cheapest?")  # asked before any search: the model's honest answer must reach the user
    assert result["message"].startswith(reply)


def test_remembered_time_of_day_shapes_the_next_search(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    search = [_request(), {"type": "tool", "name": "search_providers", "input": {}}, {"type": "text", "text": "ok"}]
    with_pref, _ = make(tmp_path / "a", [mem("remember", key="global.preferred_time_of_day", value="afternoon")] + search)
    assert with_pref.handle("nand", "Remember I prefer afternoons")["status"] == "memory_updated"
    assert with_pref.handle("nand", "Plumber in Indiranagar under 1500")["status"] == "no_match"  # the mock only has morning slots
    without, _ = make(tmp_path / "b", search)
    assert without.handle("nand", "Plumber in Indiranagar under 1500")["status"] == "ready"


def test_connection_failure_asks_the_user_to_resend(tmp_path):
    class APIConnectionError(Exception):
        pass

    class Offline:
        live = False

        def next(self, messages, tools):
            raise APIConnectionError()

    chat, provider = make(tmp_path, [])
    chat.model = Offline()
    result = chat.handle("nand", "Plumber tomorrow")
    assert result["status"] == "error" and "send that again" in result["message"] and provider.booking_count() == 0


def test_range_options_name_the_day_so_the_list_does_not_repeat_times(tmp_path):
    chat, _ = make(tmp_path, [_request(date_range="2026-11-09/2026-11-10"), {"type": "tool", "name": "search_providers", "input": {}}, {"type": "text", "text": "ok"}])
    result = chat.handle("nand", "Plumber this week under 1500")
    assert "Mon 9 Nov, 9:00 AM" in result["message"] and "Tue 10 Nov, 11:00 AM" in result["message"]


def test_empty_search_says_what_blocked_it_and_what_to_change(tmp_path):
    afternoon = [_request(time_window="afternoon", earliest_time="12:00", latest_time="17:00"), {"type": "tool", "name": "search_providers", "input": {}}, {"type": "text", "text": "ok"}]
    chat, _ = make(tmp_path, afternoon)
    result = chat.handle("nand", "Plumber tomorrow afternoon under 1500")
    assert result["status"] == "no_match" and "only have slots at 9:00 AM, 11:00 AM" in result["message"] and "widen the time window" in result["message"]
    (tmp_path / "b").mkdir()
    cheap, _ = make(tmp_path / "b", [_request(budget=500), {"type": "tool", "name": "search_providers", "input": {}}, {"type": "text", "text": "ok"}])
    result = cheap.handle("nand", "Plumber tomorrow under 500")
    assert "cheapest provider is ₹1400, above your ₹500 budget" in result["message"]


def test_question_after_an_empty_search_is_answered_from_what_was_found(tmp_path):
    reply = "Plumber 1 is the cheapest at ₹1400, but only with 9:00 AM or 11:00 AM slots."
    chat, _ = make(tmp_path, [_request(time_window="afternoon", earliest_time="12:00", latest_time="17:00"),
                              {"type": "tool", "name": "search_providers", "input": {}}, {"type": "text", "text": "ok"},
                              {"type": "text", "text": reply}])
    assert chat.handle("nand", "Plumber tomorrow afternoon under 1500")["status"] == "no_match"
    assert chat.handle("nand", "which is the cheapest?")["message"] == reply
