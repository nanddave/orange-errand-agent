import json

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from errand.gate import ActionDispatcher, MockBookingProvider, build_graph


PROPOSAL = {
    "user_id": "nand",
    "provider_id": "plumber-1",
    "slot": "2026-11-06T09:00:00+05:30",
    "price": 1400,
    "hold_id": "hold-1",
}


def test_no_booking_without_exact_approval(tmp_path):
    db = tmp_path / "business.sqlite"
    provider = MockBookingProvider(db)
    dispatcher = ActionDispatcher(db, provider, tmp_path / "trace.jsonl")
    action_id = dispatcher.propose(PROPOSAL)

    with pytest.raises(PermissionError):
        dispatcher.execute(action_id)
    assert provider.book_call_count() == 0

    dispatcher.approve(action_id, PROPOSAL)
    dispatcher.change(action_id, {**PROPOSAL, "price": 1500})
    with pytest.raises(PermissionError):
        dispatcher.execute(action_id)
    assert provider.book_call_count() == 0


def test_timeout_after_success_restart_resumes_without_second_book_call(tmp_path):
    db = tmp_path / "business.sqlite"
    checkpoints = tmp_path / "checkpoints.sqlite"
    trace = tmp_path / "trace.jsonl"
    provider = MockBookingProvider(db, timeout_after_success=True, status_error_once=True)
    dispatcher = ActionDispatcher(db, provider, trace)
    action_id = dispatcher.propose(PROPOSAL)
    config = {"configurable": {"thread_id": "booking-1"}}

    with SqliteSaver.from_conn_string(str(checkpoints)) as saver:
        graph = build_graph(dispatcher, saver)
        result = graph.invoke({"action_id": action_id}, config, durability="sync")
        assert result["__interrupt__"]
        with pytest.raises(RuntimeError, match="status temporarily unavailable"):
            graph.invoke(Command(resume={"approved": True, "proposal": PROPOSAL}), config, durability="sync")

    assert provider.booking_count() == 1
    assert provider.book_call_count() == 1

    resumed_provider = MockBookingProvider(db)
    resumed_dispatcher = ActionDispatcher(db, resumed_provider, trace)
    with SqliteSaver.from_conn_string(str(checkpoints)) as saver:
        graph = build_graph(resumed_dispatcher, saver)
        result = graph.invoke(None, config, durability="sync")

    assert result["outcome"] == "confirmed"
    assert resumed_provider.booking_count() == 1
    assert resumed_provider.book_call_count() == 1
    events = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [item["event"] for item in events] == [
        "proposed", "approved", "book_call", "booking_timeout", "status_error",
        "recovery_started", "status_checked", "confirmed"
    ]
    assert events[0]["proposal"] == PROPOSAL
    assert events[2]["idempotency_key"] == events[6]["idempotency_key"]
    assert events[-1]["message"] == "Booking confirmed."
