"""Regression tests for defects found by the independent review of the approval changes."""

import json
import threading
import urllib.error
import urllib.request

from errand.evals import FACTS, SLOT, book_held, booking_steps, call, setup
from errand.model import ScriptedModel
from errand.web import create_server

PROMPT = "Book plumber-1 Friday morning under ₹1500"


def test_redelivered_cancel_after_hold_expiry_reuses_the_live_replacement(tmp_path):
    now = [0.0]
    agent, provider = setup(tmp_path, booking_steps(), provider_args={"clock": lambda: now[0]})
    first = agent.handle("nand", PROMPT)
    assert agent.confirm(first["action_id"], True)["status"] == "confirmed"
    booking = provider.list_bookings("nand")[0]
    provider.provider_cancel(booking["id"])
    original = agent.on_provider_cancelled("nand", booking["id"])
    now[0] = 601.0
    renewed = agent.confirm(original["action_id"], True)
    assert renewed["status"] == "approval_required" and renewed["action_id"] != original["action_id"]
    again = agent.on_provider_cancelled("nand", booking["id"])
    assert again["action_id"] == renewed["action_id"]
    assert agent.confirm(renewed["action_id"], True)["status"] == "confirmed"
    assert [item["status"] for item in provider.list_bookings("nand")].count("confirmed") == 1


def test_a_booked_hold_cannot_be_proposed_again(tmp_path):
    agent, provider = setup(tmp_path, booking_steps())
    first = agent.handle("nand", PROMPT)
    hold_id = agent.dispatcher.current_proposal(first["action_id"])["hold_id"]
    assert agent.confirm(first["action_id"], True)["status"] == "confirmed"
    agent.model = ScriptedModel([call("book", hold_id=hold_id)])
    assert agent.handle("nand", "Book it again")["status"] == "blocked"
    assert provider.booking_count() == 1


def test_saved_times_must_be_valid_hh_mm(tmp_path):
    for index, (action, value, status) in enumerate([
        ("remember", "9:30", "needs_input"), ("remember", "after 10", "needs_input"),
        ("remember", "09:30", "memory_updated"), ("correct", "25:00", "needs_input"),
    ]):
        root = tmp_path / str(index)
        root.mkdir()
        agent, _ = setup(root, [call("memory_action", action=action, key="global.earliest_time", value=value)])
        agent.memory.put("nand", "global.earliest_time", "09:00", source="explicit")
        assert agent.handle("nand", "Save my earliest time")["status"] == status
        assert agent.memory.inspect("nand")[0]["value"] == ("09:30" if status == "memory_updated" else "09:00")


def test_slot_offset_must_match_the_offered_slot(tmp_path):
    for index, (slot, status) in enumerate([
        ("2026-11-06T09:00:00Z", "blocked"), ("2026-11-06T09:00:00+01:00", "blocked"),
        ("2026-11-06T09:00:00+05:30", "approval_required"), ("2026-11-06T09:00+0530", "approval_required"),
        ("2026-11-06 09:00", "approval_required"),
    ]):
        root = tmp_path / str(index)
        root.mkdir()
        agent, _ = setup(root, [call("update_request", **FACTS), call("search_providers"),
                                call("hold_slot", provider_id="plumber-1", slot=slot), book_held])
        result = agent.handle("nand", "Book plumber-1")
        assert result["status"] == status, slot
        if status == "approval_required":
            assert agent.dispatcher.current_proposal(result["action_id"])["slot"] == SLOT


def test_forgetting_before_inference_stops_the_inference(tmp_path):
    agent, provider = setup(tmp_path, booking_steps())
    agent.memory.forget("nand", "global.preferred_area")
    first = agent.handle("nand", PROMPT)
    assert agent.confirm(first["action_id"], True)["status"] == "confirmed"
    assert agent.memory.inspect("nand") == []


def _post(base, path, data):
    request = urllib.request.Request(base + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def test_simulated_cancel_is_refused_while_an_approval_is_pending(tmp_path):
    server = create_server(tmp_path, scripted=True, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        _, proposed = _post(base, "/chat", {"text": "Find a plumber"})
        assert proposed["status"] == "approval_required"
        status, body = _post(base, "/simulate-provider-cancel", {})
        assert status == 409 and "pending" in body["error"]
        _, still = _post(base, "/approve", {"action_id": proposed["action_id"], "approved": False})
        assert still["status"] == "not_approved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_hidden_attribute_wins_over_flex_layout():
    from errand.web import PAGE

    assert "[hidden]{display:none!important}" in PAGE


def test_a_changed_action_can_still_be_declined(tmp_path):
    agent, provider = setup(tmp_path, booking_steps())
    shown = agent.handle("nand", PROMPT)
    original = agent.dispatcher.current_proposal(shown["action_id"])
    agent.dispatcher.change(shown["action_id"], {**original, "price": 9000})
    try:
        agent.confirm(shown["action_id"], True)
    except PermissionError:
        pass
    else:
        raise AssertionError("changed action used the old approval")
    assert agent.confirm(shown["action_id"], False)["status"] == "not_approved"
    assert agent.dispatcher.status(shown["action_id"]) == "not_approved" and provider.book_call_count() == 0
