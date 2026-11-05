import pytest

from errand.gate import ActionDispatcher, MockBookingProvider


def booked_provider(tmp_path):
    db = tmp_path / "business.sqlite"
    tools = MockBookingProvider(db, scenario="success", user_id="nand")
    hold = tools.hold_slot("plumber-1", "2026-11-06T09:00:00+05:30")
    tools.book(hold["hold_id"], "seed:booking")
    return db, tools, tools.list_bookings("nand")[0]


def test_cancel_requires_exact_approval_and_retries_503_once(tmp_path):
    db, tools, booking = booked_provider(tmp_path)
    dispatcher = ActionDispatcher(db, tools, tmp_path / "trace.jsonl")
    proposal = {
        "action": "cancel", "user_id": "nand", "booking_id": booking["id"],
        "provider_id": booking["provider"], "slot": booking["slot"], "price": 1400,
    }
    action_id = dispatcher.propose(proposal)
    with pytest.raises(PermissionError):
        dispatcher.execute(action_id)
    assert tools.list_bookings("nand")[0]["status"] == "confirmed"

    dispatcher.approve(action_id, proposal)
    assert dispatcher.execute(action_id) == "cancelled"
    assert tools.list_bookings("nand")[0]["status"] == "cancelled"


def test_reschedule_conflict_keeps_original_slot_and_changed_price_voids_approval(tmp_path):
    db, tools, booking = booked_provider(tmp_path)
    tools.conflict_slots.add("busy")
    dispatcher = ActionDispatcher(db, tools, tmp_path / "trace.jsonl")
    proposal = {
        "action": "reschedule", "user_id": "nand", "booking_id": booking["id"],
        "provider_id": booking["provider"], "slot": "busy", "price": 1400,
    }
    action_id = dispatcher.propose(proposal)
    dispatcher.approve(action_id, proposal)
    assert dispatcher.execute(action_id) == "slot_conflict"
    assert tools.list_bookings("nand")[0]["slot"] == booking["slot"]

    with pytest.raises(RuntimeError, match="completed"):
        dispatcher.change(action_id, {**proposal, "slot": "2026-11-07T11:00:00+05:30"})
    action_id = dispatcher.propose({**proposal, "slot": "2026-11-07T11:00:00+05:30"})
    with pytest.raises(PermissionError):
        dispatcher.execute(action_id)


def test_pending_action_cannot_be_changed_to_different_provider(tmp_path):
    db = tmp_path / "business.sqlite"
    tools = MockBookingProvider(db, timeout_after_success=True, status_error_once=True)
    dispatcher = ActionDispatcher(db, tools, tmp_path / "trace.jsonl")
    hold = tools.hold_slot("plumber-1", "2026-11-06T09:00:00+05:30")
    proposal = {
        "user_id": "nand", "provider_id": "plumber-1", "slot": "2026-11-06T09:00:00+05:30",
        "price": hold["price"], "hold_id": hold["hold_id"],
    }
    action_id = dispatcher.propose(proposal)
    dispatcher.approve(action_id, proposal)
    with pytest.raises(RuntimeError, match="status temporarily unavailable"):
        dispatcher.execute(action_id)

    with pytest.raises(RuntimeError, match="pending"):
        dispatcher.change(action_id, {**proposal, "provider_id": "plumber-2"})
    assert dispatcher.execute(action_id) == "confirmed"
    assert tools.booking_count() == 1
