import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from errand.evals import FACTS, SLOT, call
from errand.gate import MockBookingProvider
from errand.model import ScriptedModel
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
            assert b"What the assistant is doing" in response.read()
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


def test_page_has_new_strings_and_no_innerhtml():
    for text in ["How this works", "I never book without your yes", "Pretend the provider cancels my booking",
                 "What the assistant is doing", "Show technical detail", "Yes, book it", "No, don't",
                 "Searched and found", "Flagged a risky review and ignored it", "Held the slot for 10 minutes",
                 "The provider didn't answer in time", "Hold expired: new hold, new approval needed",
                 "SAFETY = a guardrail acted", "/select", "id=\"simcancel\"", "id=\"approve\"", "id=\"deny\""]:
        assert text in PAGE, text
    assert "innerHTML" not in PAGE and "insertAdjacentHTML" not in PAGE


def test_page_has_waiting_summary_and_readable_time_function():
    for text in ["Thinking", "Working on it", "Last run:", "readableTimes", "id=\"runsum\""]:
        assert text in PAGE, text
    assert "innerHTML" not in PAGE and "insertAdjacentHTML" not in PAGE


def test_trace_and_page_stay_live_while_chat_is_in_flight_and_second_post_is_409(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow(messages, tools):
        started.set()
        release.wait(10)
        return {"type": "text", "text": "Hello"}

    server = create_server(tmp_path, scripted=True, port=0, model=ScriptedModel([slow]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(text):
        request = Request(base + "/chat", json.dumps({"text": text}).encode(), {"Content-Type": "application/json"})
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    first = {}
    worker = threading.Thread(target=lambda: first.update(post("plumber")), daemon=True)
    try:
        worker.start()
        assert started.wait(5)
        for path in ["/trace", "/pending", "/"]:
            with urlopen(base + path, timeout=1) as response:
                assert response.status == 200
        with pytest.raises(HTTPError) as error:
            post("again")
        assert error.value.code == 409
        assert json.load(error.value) == {"error": "Still working on your last request. Please wait."}
    finally:
        release.set()
        worker.join(timeout=10)
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert first.get("message")


def _ready_server(tmp_path):
    steps = [call("update_request", **FACTS), call("search_providers"), {"type": "text", "text": "Ready"}]
    server = create_server(tmp_path, scripted=True, scenario="success", port=0, model=ScriptedModel(steps))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def req(path, data=None, headers=None):
        request = Request(base + path, None if data is None else json.dumps(data).encode(),
                          {"Content-Type": "application/json", **(headers or {})})
        with urlopen(request) as response:
            return json.load(response)

    def stop():
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    return req, stop


def test_select_flow_books_exactly_once_and_pending_is_structured(tmp_path):
    req, stop = _ready_server(tmp_path)
    try:
        assert req("/pending") == {"has_confirmed": False}
        ready = req("/chat", {"text": "plumber"})
        assert ready["status"] == "ready" and ready["options"][0]["slots"][0]["label"]
        option = ready["options"][0]
        proposal = req("/select", {"provider_id": option["provider_id"], "slot": option["slots"][0]["value"]})
        assert proposal["status"] == "approval_required"
        pending = req("/pending")
        assert pending["has_confirmed"] is False and pending["action_id"] == proposal["action_id"]
        assert pending["proposal"] == {"action": "book", "provider_id": option["provider_id"],
                                       "slot": option["slots"][0]["value"],
                                       "slot_label": option["slots"][0]["label"], "price": option["price"]}
        assert "hold_id" not in json.dumps(pending)
        with pytest.raises(HTTPError) as error:
            req("/select", {"provider_id": option["provider_id"], "slot": option["slots"][0]["value"]})
        assert error.value.code == 409 and json.load(error.value) == {"error": "resolve the pending action first"}
        assert req("/approve", {"action_id": proposal["action_id"], "approved": True})["status"] == "confirmed"
        assert req("/pending")["has_confirmed"] is True
        provider = MockBookingProvider(tmp_path / "business.sqlite", user_id="demo")
        assert [b["status"] for b in provider.list_bookings("demo")] == ["confirmed"]
    finally:
        stop()


def test_select_rejects_bad_types_and_foreign_origin(tmp_path):
    req, stop = _ready_server(tmp_path)
    try:
        for body in [{"provider_id": 1, "slot": "x"}, {"provider_id": "plumber-1"}, {"provider_id": "a", "slot": None}]:
            with pytest.raises(HTTPError) as error:
                req("/select", body)
            assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            req("/select", {"provider_id": "plumber-1", "slot": SLOT}, {"Origin": "http://evil.example"})
        assert error.value.code == 403
    finally:
        stop()


def test_pending_kind_and_replacement_context_and_bookings(tmp_path):
    req, stop = _ready_server(tmp_path)
    try:
        assert req("/bookings") == {"bookings": []}
        ready = req("/chat", {"text": "plumber"})
        option = ready["options"][0]
        proposal = req("/select", {"provider_id": option["provider_id"], "slot": option["slots"][0]["value"]})
        pending = req("/pending")
        assert pending["kind"] == "booking" and "replaces" not in pending
        assert req("/approve", {"action_id": proposal["action_id"], "approved": True})["status"] == "confirmed"
        listed = req("/bookings")["bookings"]
        assert len(listed) == 1 and set(listed[0]) == {"id", "provider", "slot_label", "price", "status"}
        assert listed[0]["provider"] == option["provider_id"].replace("-", " ").title()
        assert listed[0]["slot_label"] == option["slots"][0]["label"] and listed[0]["price"] == option["price"]
        assert listed[0]["status"] == "confirmed"
        result = req("/simulate-provider-cancel", {})
        assert result["status"] == "approval_required"
        assert req("/bookings")["bookings"][0]["status"] == "cancelled"
        pending = req("/pending")
        assert pending["kind"] == "replacement" and pending["action_id"] == result["action_id"]
        assert pending["replaces"] == {"provider": listed[0]["provider"], "slot_label": listed[0]["slot_label"]}
    finally:
        stop()


def test_bookings_takes_no_lock_while_chat_is_in_flight(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow(messages, tools):
        started.set()
        release.wait(10)
        return {"type": "text", "text": "Hello"}

    server = create_server(tmp_path, scripted=True, port=0, model=ScriptedModel([slow]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    request = Request(base + "/chat", json.dumps({"text": "plumber"}).encode(), {"Content-Type": "application/json"})
    worker = threading.Thread(target=lambda: urlopen(request, timeout=15).read(), daemon=True)
    try:
        worker.start()
        assert started.wait(5)
        with urlopen(base + "/bookings", timeout=1) as response:
            assert json.load(response) == {"bookings": []}
    finally:
        release.set()
        worker.join(timeout=10)
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_page_has_hero_cards_bookings_strip_and_storage():
    for text in ["approval-card", "confirm-card", "Nothing is booked until you click Yes.", "Your bookings",
                 "sessionStorage", "Watch the assistant work here", "Approve a replacement?",
                 "Replacement waiting for your yes", "Booked. One booking, verified.", "/bookings",
                 "nothing was booked twice"]:
        assert text in PAGE, text
    assert "innerHTML" not in PAGE and "insertAdjacentHTML" not in PAGE


def test_page_javascript_parses(tmp_path):
    import re, shutil, subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    script = re.search(r"<script>(.*)</script>", PAGE, re.S).group(1)
    path = tmp_path / "page.js"
    path.write_text(script)
    try:
        assert subprocess.run([node, "--check", str(path)], capture_output=True, text=True).returncode == 0
    finally:
        path.unlink()


def test_page_visual_identity_and_offline():
    for text in [":focus-visible", "prefers-reduced-motion", "--accent:", "Book home services. You approve every step.",
                 "<title>Orange Errand Agent</title>", "Orange Errand Agent"]:
        assert text in PAGE, text
    assert "https://" not in PAGE and "//cdn" not in PAGE and "fonts.googleapis" not in PAGE
    assert "@import" not in PAGE and "<link" not in PAGE and "src=" not in PAGE


def test_chat_body_leaves_room_for_the_approval_card_and_input():
    from errand.web import PAGE

    assert "min-height:140px" in PAGE and "min-height:300px" not in PAGE
