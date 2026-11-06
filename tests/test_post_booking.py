import json
import socket
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from errand.evals import FACTS, booking_steps, call, setup
from errand.model import ScriptedModel
from errand.web import create_server


def booked(tmp_path, scenario="success"):
    agent, provider = setup(tmp_path, booking_steps(), scenario)
    action_id = agent.handle("nand", "Plumber in Indiranagar November 6 under 1500")["action_id"]
    assert agent.confirm(action_id, True)["status"] == "confirmed"
    return agent, provider, action_id


def test_status_question_after_booking_reports_the_booking_not_old_options(tmp_path):
    agent, provider, _ = booked(tmp_path)
    agent.model = ScriptedModel([{"type": "text", "text": "Options: stale"}])
    result = agent.handle("nand", "Is my booking confirmed?")
    assert result["status"] == "ready" and "options" not in result
    assert "Your bookings:" in result["message"] and "(confirmed)" in result["message"]
    assert "Options:" not in result["message"] and "No booking has been made" not in result["message"]
    assert provider.booking_count() == 1


def test_finished_booking_clears_search_state_so_options_are_not_reshown(tmp_path):
    agent, _, _ = booked(tmp_path)
    state = agent._load("nand")[2]
    assert "candidates" not in state and "searched" not in state


def test_declining_keeps_the_options_available(tmp_path):
    agent, _ = setup(tmp_path, booking_steps())
    action_id = agent.handle("nand", "go")["action_id"]
    assert agent.confirm(action_id, False)["status"] == "not_approved"
    assert agent._load("nand")[2].get("candidates")


def test_concurrent_confirms_reach_the_provider_once(tmp_path):
    agent, provider = setup(tmp_path, booking_steps(), "success")
    action_id = agent.handle("nand", "go")["action_id"]
    outcomes = []
    threads = [threading.Thread(target=lambda: outcomes.append(agent.confirm(action_id, True)["status"])) for _ in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert set(outcomes) == {"confirmed"} and provider.book_call_count() == 1 and provider.booking_count() == 1


def test_absurd_budget_is_blocked(tmp_path):
    agent, provider = setup(tmp_path, [call("update_request", **{**FACTS, "budget": 10**15})])
    result = agent.handle("nand", "go")
    assert result["status"] == "blocked" and "No booking was made" in result["message"]


def serve(tmp_path):
    server = create_server(tmp_path, scripted=True, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def raw_post(port, headers):
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(("POST /chat HTTP/1.1\r\nHost: x\r\n" + headers + "\r\n\r\n").encode())
        return sock.recv(200).split(b"\r\n")[0]


def test_non_numeric_content_length_is_a_400(tmp_path):
    server, port = serve(tmp_path)
    try:
        assert b"400" in raw_post(port, "Content-Type: application/json\r\nContent-Length: abc")
    finally:
        server.shutdown()


def test_chat_text_must_be_a_string(tmp_path):
    server, port = serve(tmp_path)
    try:
        request = Request(f"http://127.0.0.1:{port}/chat", json.dumps({"text": {"a": 1}}).encode(), {"Content-Type": "application/json"})
        try:
            urlopen(request)
            assert False, "expected 400"
        except HTTPError as error:
            assert error.code == 400
    finally:
        server.shutdown()


def test_stalled_body_does_not_hold_the_request_lock(tmp_path):
    server, port = serve(tmp_path)
    stalled = socket.create_connection(("127.0.0.1", port))
    try:
        stalled.sendall(b"POST /chat HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
        request = Request(f"http://127.0.0.1:{port}/chat", json.dumps({"text": "hi"}).encode(), {"Content-Type": "application/json"})
        assert urlopen(request, timeout=5).status == 200
    finally:
        stalled.close()
        server.shutdown()


def test_status_questions_never_reach_the_model_but_requests_still_do(tmp_path):
    agent, provider, _ = booked(tmp_path)
    searched = ScriptedModel([call("search_providers"), {"type": "text", "text": "x"}])
    for phrase in ["give me my booking confirmation", "Is my booking confirmed?", "what is the status of my booking", "did it go through?"]:
        agent.model = searched
        result = agent.handle("nand", phrase)
        assert result["message"].startswith("Your bookings:") and "options" not in result, phrase
    assert len(list(searched.steps)) == 2  # the model was never asked
    agent.model = ScriptedModel([call("update_request", **FACTS), call("search_providers"), {"type": "text", "text": "x"}])
    assert agent.handle("nand", "Book another plumber")["status"] == "ready"
    assert agent.facts("nand")["service"] == "plumber"
