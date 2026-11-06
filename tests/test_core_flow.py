import json

import pytest

from errand.agent import _slot_label
from errand.model import ScriptedModel
from errand.evals import FACTS, SLOT, book_held, call, setup

SEARCH = [call("update_request", **FACTS), call("search_providers"), {"type": "text", "text": "ok"}]


def searched(tmp_path, extra=()):
    agent, provider = setup(tmp_path, [*SEARCH, *extra])
    return agent, provider, agent.handle("nand", "Plumber in Indiranagar under 1500")


def events(agent):
    return [json.loads(line) for line in agent.dispatcher.trace.read_text().splitlines()]


def test_slot_label_and_fallback():
    assert _slot_label(SLOT) == "Fri 6 Nov, 9:00 AM"
    assert _slot_label("2026-11-06T13:05:00+05:30") == "Fri 6 Nov, 1:05 PM"
    assert _slot_label("junk") == "junk"


def test_ready_result_has_options_and_slot_labels(tmp_path):
    _, _, result = searched(tmp_path)
    assert result["status"] == "ready" and "No booking has been made." in result["message"]
    assert "plumber-1 ₹1400 (4.7★): 9:00 AM, 11:00 AM" in result["message"]
    first = result["options"][0]
    assert first["name"] == "Plumber 1" and first["price"] == 1400 and first["rating"] == 4.7
    assert [s["value"] for s in first["slots"]] == [SLOT, "2026-11-06T11:00:00+05:30"]


def test_missing_lists_all_and_agrees_across_paths(tmp_path):
    agent, _ = setup(tmp_path, [{"type": "text", "text": "hi"}, call("update_request", service="plumber"),
                                call("search_providers")])
    first = agent.handle("nand", "hi")
    assert first["missing"] == ["service", "area", "date_range", "budget"]
    assert first["message"] == "hi I still need: service, area, date range, budget."  # model wording, then the code-owned list
    second = agent.handle("nand", "plumber")
    assert second["missing"] == ["area", "date_range", "budget"]
    third = agent.handle("nand", "search")
    assert third["status"] == "needs_input" and third["missing"] == ["area", "date_range", "budget"]


def test_prompt_has_options_and_holds_but_not_old_chat(tmp_path):
    seen = []

    def spy(messages, _tools):
        seen.append(messages[0]["content"])
        return call("hold_slot", provider_id="plumber-1", slot=SLOT)

    agent, _, _ = searched(tmp_path)
    agent.model = ScriptedModel([{"type": "text", "text": "ok"}])
    agent.handle("nand", "SECRET-OLD-CHAT")
    agent.model = ScriptedModel([spy, book_held])
    agent.handle("nand", "plumber-1 at 9")
    agent.model = ScriptedModel([lambda m, t: seen.append(m[0]["content"]) or {"type": "text", "text": "x"}])
    agent.handle("nand", "again")
    assert "plumber-1" in seen[0] and SLOT in seen[0] and "Current options" in seen[0]
    assert "Held slots:" in seen[-1] and "plumber-1 at " + SLOT in seen[-1]
    assert "Latest: again" in seen[-1] and "SECRET-OLD-CHAT" not in seen[-1]


def test_search_no_longer_blocks_update_but_get_provider_does(tmp_path):
    agent, provider = setup(tmp_path, [call("update_request", **FACTS), call("search_providers"),
                                       call("update_request", budget=1700), {"type": "text", "text": "ok"}])
    assert agent.handle("nand", "go")["status"] == "ready" and agent.facts("nand")["budget"] == 1700
    (tmp_path / "b").mkdir()
    agent2, _ = setup(tmp_path / "b",
                      [call("update_request", **FACTS), call("search_providers"),
                       call("get_provider", provider_id="plumber-1"), call("update_request", budget=9999)])
    assert agent2.handle("nand", "go")["status"] == "blocked" and agent2.facts("nand")["budget"] == 1500


def test_select_proposes_only_then_separate_approval_books(tmp_path):
    agent, provider, _ = searched(tmp_path)
    result = agent.select("nand", "plumber-1", SLOT)
    assert result["status"] == "approval_required" and result["action_id"]
    assert result["message"].startswith("Approve booking plumber-1")
    assert provider.book_call_count() == 0
    names = [(e["event"], e.get("tool")) for e in events(agent)]
    assert names.index(("select_clicked", None)) < names.index(("tool_call", "hold_slot")) < names.index(("tool_result", "hold_slot"))
    assert agent.confirm(result["action_id"], True)["status"] == "confirmed"
    assert provider.book_call_count() == 1


def test_select_blocks_unknown_wrong_offset_and_over_budget(tmp_path):
    agent, provider, _ = searched(tmp_path)
    for pid, slot in [("nope-9", SLOT), ("plumber-1", "2026-11-06T10:00:00+05:30"),
                      ("plumber-1", "2026-11-06T09:00:00Z")]:
        assert agent.select("nand", pid, slot)["status"] == "blocked"
    assert provider.book_call_count() == 0
    provider.quote_delta = 500  # quote now exceeds the 1500 budget
    assert agent.select("nand", "plumber-1", SLOT)["status"] == "blocked"


def test_select_without_facts_needs_input(tmp_path):
    agent, _ = setup(tmp_path, [])
    result = agent.select("nand", "plumber-1", SLOT)
    assert result["status"] == "needs_input" and len(result["missing"]) == 4


def test_repeat_select_after_confirmed_booking_is_blocked(tmp_path):
    agent, provider, _ = searched(tmp_path)
    first = agent.select("nand", "plumber-1", SLOT)
    assert agent.confirm(first["action_id"], True)["status"] == "confirmed"
    again = agent.select("nand", "plumber-1", SLOT)
    assert again["status"] == "blocked" and provider.booking_count() == 1 and provider.book_call_count() == 1


def test_free_text_preferences_do_not_discard_the_current_options(tmp_path):
    from errand.evals import FACTS, SLOT, book_held, call, setup

    agent, provider = setup(tmp_path, [call("update_request", **FACTS), call("search_providers"),
                                       call("update_request", preferences="same electrician as last time"),
                                       call("hold_slot", provider_id="plumber-1", slot=SLOT), book_held])
    assert agent.handle("nand", "Book plumber-1")["status"] == "approval_required"
    assert provider.book_call_count() == 0


def test_tool_descriptions_steer_the_model_away_from_live_misroutes():
    from errand.agent import TOOLS

    text = {tool["name"]: tool["description"] for tool in TOOLS}
    assert "last time" in text["list_bookings"] and "cancel" in text["list_bookings"]
    assert "global.earliest_time" in text["memory_action"] and "never to find past bookings" in text["memory_action"]
    assert "choose among the current options" in text["update_request"]
