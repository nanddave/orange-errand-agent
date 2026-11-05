from errand.agent import ChatAgent
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore
from errand.model import ScriptedModel


def test_provider_cancellation_triggers_notice_and_fresh_approval_without_user_prompt(tmp_path):
    db = tmp_path / "business.sqlite"
    tools = MockBookingProvider(db, scenario="success", user_id="nand")
    old_hold = tools.hold_slot("plumber-1", "2026-11-06T09:00:00+05:30")
    tools.book(old_hold["hold_id"], "old-booking")
    old_booking = tools.list_bookings("nand")[0]
    dispatcher = ActionDispatcher(db, tools, tmp_path / "trace.jsonl")
    memory = MemoryStore(db)
    memory.put("nand", "global.earliest_time", "09:00", source="explicit")
    chat = ChatAgent(db, tools, dispatcher, memory, ScriptedModel([
        {"type": "tool", "name": "update_request", "input": {
            "service": "plumber", "area": "Indiranagar", "date_range": "2026-11-06", "budget": 1700
        }},
        {"type": "text", "text": "Ready."},
    ]))
    chat.handle("nand", "Plumber in Indiranagar Friday, under 1700")
    tools.provider_cancel(old_booking["id"])

    notice = chat.on_provider_cancelled("nand", old_booking["id"])
    assert notice["status"] == "approval_required"
    assert "cancelled" in notice["message"].lower()
    assert "plumber-2" in notice["message"]
    assert tools.booking_count() == 1
    repeated = chat.on_provider_cancelled("nand", old_booking["id"])
    assert repeated["action_id"] == notice["action_id"]
    assert chat.confirm(notice["action_id"], True)["status"] == "confirmed"
    assert tools.booking_count() == 2
    assert chat.on_provider_cancelled("nand", old_booking["id"])["status"] == "already_handled"
    assert tools.booking_count() == 2
