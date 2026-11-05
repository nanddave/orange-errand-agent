import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from errand.gate import MockBookingProvider
from errand.web import PAGE, create_server


def test_local_page_shows_trace_and_requires_matching_approval(tmp_path):
    server = create_server(tmp_path, scripted=True, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, data):
        request = Request(base + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.load(response)

    try:
        with urlopen(base) as response:
            assert b"Execution trace" in response.read()
        proposal = post("/chat", {"text": "Book plumber-1 on Friday morning under 1500"})
        assert proposal["status"] == "approval_required"
        with urlopen(base + "/trace") as response:
            events = json.load(response)
        assert any(item["event"] == "proposed" for item in events)
        confirmed = post("/approve", {"action_id": proposal["action_id"], "approved": True})
        assert confirmed["status"] == "confirmed"
        with urlopen(base + "/trace") as response:
            events = json.load(response)
        assert any(item["event"] == "booking_timeout" for item in events)
        assert any(item["event"] == "status_checked" and item["outcome"] == "confirmed" for item in events)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_page_restores_pending_approval_after_restart(tmp_path):
    server = create_server(tmp_path, scripted=True, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    request = Request(base + "/chat", json.dumps({"text": "Book a plumber"}).encode(),
                      {"Content-Type": "application/json"})
    with urlopen(request) as response:
        proposal = json.load(response)
    server.shutdown(); server.server_close(); thread.join(timeout=2)

    restarted = create_server(tmp_path, scripted=True, port=0)
    next_thread = threading.Thread(target=restarted.serve_forever, daemon=True)
    next_thread.start()
    next_base = f"http://127.0.0.1:{restarted.server_address[1]}"
    try:
        with urlopen(next_base + "/pending") as response:
            pending = json.load(response)
        assert pending["action_id"] == proposal["action_id"]
        request = Request(next_base + "/approve", json.dumps({"action_id": proposal["action_id"],
                                                                "approved": True}).encode(),
                          {"Content-Type": "application/json"})
        with urlopen(request) as response:
            assert json.load(response)["status"] == "confirmed"
    finally:
        restarted.shutdown(); restarted.server_close(); next_thread.join(timeout=2)


def _serve(tmp_path, scenario="timeout_success"):
    server = create_server(tmp_path, scripted=True, scenario=scenario, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(path, data):
        request = Request(base + path, json.dumps(data).encode(), {"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.load(response)

    def stop():
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    return post, stop


def test_simulate_cancel_without_confirmed_booking_is_409(tmp_path):
    post, stop = _serve(tmp_path)
    try:
        with pytest.raises(HTTPError) as error:
            post("/simulate-provider-cancel", {})
        assert error.value.code == 409
        assert json.load(error.value) == {"error": "no confirmed booking"}
    finally:
        stop()


def test_simulate_cancel_cancels_provider_side_and_waits_for_approval(tmp_path):
    post, stop = _serve(tmp_path, "success")
    try:
        proposal = post("/chat", {"text": "Book plumber-1 on Friday morning under 1500"})
        assert post("/approve", {"action_id": proposal["action_id"], "approved": True})["status"] == "confirmed"
        provider = MockBookingProvider(tmp_path / "business.sqlite", user_id="demo")
        original = provider.list_bookings("demo")
        assert [b["status"] for b in original] == ["confirmed"]
        result = post("/simulate-provider-cancel", {})
        assert result["status"] == "approval_required" and result["action_id"]
        after = provider.list_bookings("demo")
        assert [(b["id"], b["status"]) for b in after] == [(original[0]["id"], "cancelled")]
    finally:
        stop()


def test_page_has_cancel_button_and_no_innerhtml():
    assert "Simulate provider cancellation" in PAGE
    assert "innerHTML" not in PAGE
