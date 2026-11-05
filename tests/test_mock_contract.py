import pytest

from errand.gate import MockBookingProvider


def test_search_provider_and_hold_contract(tmp_path):
    now = [1000.0]
    tools = MockBookingProvider(tmp_path / "mock.sqlite", clock=lambda: now[0], quote_delta=100)

    matches = tools.search_providers("plumber", "Indiranagar", "2026-11-06")
    assert 3 <= len(matches) <= 8
    assert all({"id", "price", "rating", "slots", "attributes"} <= set(p) for p in matches)
    details = tools.get_provider(matches[0]["id"])
    assert details["id"] == matches[0]["id"]
    assert any("ignore" in review.lower() for review in details["reviews"])
    assert {"name", "service", "price", "rating", "attributes"} <= set(details)
    assert details["name"] == "Plumber 1 Services" and details["price"] == matches[0]["price"]
    assert details["rating"] == matches[0]["rating"]
    other = tools.get_provider(matches[1]["id"])
    assert len(other["reviews"]) == 2
    assert not any("ignore" in review.lower() for review in other["reviews"])
    assert any("ignore" in r.lower() for r in tools.get_provider("electrician-1")["reviews"])

    hold = tools.hold_slot(matches[0]["id"], matches[0]["slots"][0])
    assert hold["expires_at"] == 1600.0
    assert hold["price"] == matches[0]["price"] + 100
    now[0] = 1601.0
    assert tools.book(hold["hold_id"], "book:expired") == "hold_expired"
    assert tools.booking_count() == 0


@pytest.mark.parametrize(
    ("scenario", "result", "booked"),
    [
        ("success", "confirmed", True),
        ("slot_taken", "slot_taken", False),
        ("timeout_success", "timeout", True),
        ("timeout_failure", "timeout", False),
    ],
)
def test_booking_scenarios_are_reproducible(tmp_path, scenario, result, booked):
    tools = MockBookingProvider(tmp_path / "mock.sqlite", scenario=scenario)
    hold = tools.hold_slot("plumber-1", "2026-11-06T09:00:00+05:30")
    if result == "timeout":
        with pytest.raises(TimeoutError):
            tools.book(hold["hold_id"], "book:one")
    else:
        assert tools.book(hold["hold_id"], "book:one") == result
    assert (tools.get_booking_status("book:one") == "confirmed") is booked


def test_cancel_reschedule_and_list_bookings(tmp_path):
    tools = MockBookingProvider(tmp_path / "mock.sqlite", user_id="nand", conflict_slots={"busy"})
    hold = tools.hold_slot("plumber-1", "2026-11-06T09:00:00+05:30")
    assert tools.book(hold["hold_id"], "book:one") == "confirmed"
    booking = tools.list_bookings("nand")[0]
    assert {"id", "provider", "service", "slot", "status"} <= set(booking)
    assert tools.reschedule(booking["id"], "busy", "reschedule:one") == "slot_conflict"
    assert tools.list_bookings("nand")[0]["slot"] == booking["slot"]
    with pytest.raises(RuntimeError, match="503"):
        tools.cancel(booking["id"], "cancel:one")
    assert tools.cancel(booking["id"], "cancel:one") == "cancelled"
    assert tools.list_bookings("nand")[0]["status"] == "cancelled"
