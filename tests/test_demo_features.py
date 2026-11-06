import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from errand.evals import FACTS, SLOT, booking_steps, call, setup
from errand.model import ScriptedModel
from errand.web import PAGE, create_server


def events(root):
    return [json.loads(line) for line in (root / "trace.jsonl").read_text().splitlines()]


def test_price_change_at_hold_is_traced_and_approval_covers_the_new_price(tmp_path):
    agent, provider = setup(tmp_path, booking_steps(), "success")
    provider.quote_delta = 50
    result = agent.handle("nand", "Plumber in Indiranagar November 6 under 1500")
    assert result["status"] == "approval_required" and "₹1450" in result["message"]
    change = [e for e in events(tmp_path) if e["event"] == "price_changed"]
    assert change and change[0]["was"] == 1400 and change[0]["now"] == 1450
    assert provider.book_call_count() == 0


def test_model_wording_is_used_for_small_talk_but_never_for_claims(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    agent, _ = setup(tmp_path / "a", [{"type": "text", "text": "Happy to help. Want mornings only?"}])
    agent._save("nand", FACTS, [], {})
    first = agent.handle("nand", "Thanks, what do you suggest?")
    assert first["message"] == "Happy to help. Want mornings only?" and first["status"] == "ready"
    claimer, provider = setup(tmp_path / "b", [{"type": "text", "text": "Done, I booked it for you."}])
    claimer._save("nand", FACTS, [], {})
    result = claimer.handle("nand", "Book it")
    assert "booked it" not in result["message"] and provider.booking_count() == 0


def test_page_has_intake_form_and_break_controls():
    for text in ['id="intake"', "Start with a short form", "Try to break it", 'data-break="timeout"', 'data-break="price"',
                 'data-break="reviews"', "Price changed from", "Find options"]:
        assert text in PAGE, text
    assert "innerHTML" not in PAGE


def test_break_endpoint_switches_the_mock_and_rejects_unknown_kinds(tmp_path):
    server = create_server(tmp_path, scripted=True, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def post(kind):
        request = Request(base + "/break", json.dumps({"kind": kind}).encode(), {"Content-Type": "application/json"})
        with urlopen(request) as response:
            return json.load(response)

    try:
        assert post("price") == {"status": "ok", "kind": "price"}
        assert post("normal")["status"] == "ok"
        try:
            post("explode")
            assert False, "expected 400"
        except HTTPError as error:
            assert error.code == 400
    finally:
        server.shutdown()


def test_greeting_and_options_questions_get_a_natural_reply_without_losing_the_options(tmp_path):
    from errand.evals import SLOT
    agent, provider = setup(tmp_path, [call("update_request", **FACTS), call("search_providers"), {"type": "text", "text": "x"},
                                       {"type": "text", "text": "Plumber 1 is the cheaper one at ₹1400."}])
    first = agent.handle("nand", "Plumber in Indiranagar November 6 under 1500")
    assert first["message"].startswith("Options:") and first["options"]
    follow = agent.handle("nand", "Which one is cheaper?")
    assert follow["message"] == "Plumber 1 is the cheaper one at ₹1400." and follow["options"] == first["options"]
    assert provider.book_call_count() == 0


def test_greeting_with_no_details_is_answered_and_still_lists_what_is_missing(tmp_path):
    agent, _ = setup(tmp_path, [{"type": "text", "text": "Hello! What do you need help with?"}])
    result = agent.handle("nand", "hello")
    assert result["status"] == "needs_input" and result["message"].startswith("Hello! What do you need help with?")
    assert result["missing"] == ["service", "area", "date_range", "budget"]
