import json
from argparse import Namespace

import pytest

from errand import web
from errand.evals import FACTS, SLOT, call, setup
from errand.model import RecordingModel, ReplayModel, ScriptedModel

TURNS = [call("update_request", **FACTS), call("search_providers"),
         call("hold_slot", provider_id="plumber-1", slot=SLOT), call("book", hold_id="STALE-HOLD")]


def cassette(tmp_path, steps=TURNS):
    path = tmp_path / "c.json"
    path.write_text(json.dumps(steps))
    return path


def replay_agent(tmp_path, steps=TURNS):
    agent, provider = setup(tmp_path, [])
    agent.model = ReplayModel(cassette(tmp_path, steps))
    return agent, provider


def test_recording_round_trips_without_secrets(tmp_path):
    steps = [{"type": "text", "text": "hi", "usage": {"input_tokens": 1, "output_tokens": 2}}, call("search_providers")]
    path = tmp_path / "rec.json"
    model = RecordingModel(ScriptedModel(steps), path)
    assert [model.next([], []), model.next([], [])] == steps
    assert json.loads(path.read_text()) == steps and not list(tmp_path.glob("*.tmp"))
    assert "api_key" not in path.read_text() and "sk-" not in path.read_text()
    assert model.live is False and RecordingModel(type("L", (), {"live": True})(), path).live is True


def test_replay_substitutes_new_hold_id_and_books_once(tmp_path):
    agent, provider = replay_agent(tmp_path)
    proposal = agent.handle("nand", "Book plumber-1 Friday morning under 1500")
    assert proposal["status"] == "approval_required" and provider.book_call_count() == 0
    assert "STALE-HOLD" not in json.dumps(agent.dispatcher.current_proposal(proposal["action_id"]))
    assert agent.confirm(proposal["action_id"], True)["status"] == "confirmed"
    assert provider.book_call_count() == 1 and provider.booking_count() == 1


def test_exhausted_cassette_errors_without_booking(tmp_path):
    agent, provider = replay_agent(tmp_path, TURNS[:2])
    result = agent.handle("nand", "Book plumber-1")
    assert result["status"] == "error" and provider.book_call_count() == 0


def test_build_model(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "AnthropicModel", lambda: type("Fake", (), {"live": True})())
    monkeypatch.setattr(web, "load_local_key", lambda _path: None)
    base = dict(scripted=False, replay=None, record=None)
    assert isinstance(web.build_model(Namespace(**{**base, "replay": str(cassette(tmp_path))})), ReplayModel)
    recorder = web.build_model(Namespace(**{**base, "record": str(tmp_path / "r.json")}))
    assert isinstance(recorder, RecordingModel) and recorder.live
    assert web.build_model(Namespace(**base)) is None


def test_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit, match="mutually exclusive"):
        web.build_model(Namespace(scripted=True, replay="a.json", record=None))
    with pytest.raises(SystemExit, match="mutually exclusive"):
        web.build_model(Namespace(scripted=False, replay="a.json", record="b.json"))
