import json

from errand.memory import MemoryStore


def test_inspect_correct_forget_and_context_filter(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    for number in range(50):
        store.put("nand", f"electrician.fact_{number}", f"irrelevant-{number}", source="explicit")
    store.put("nand", "global.earliest_time", "10:00", source="explicit")
    store.put("nand", "plumber.preferred_area", "Indiranagar", source="explicit")
    store.put("nand", "plumber.technician", "unconfirmed-person", source="inferred")
    store.put("nand", "plumber.old_preference", "forgotten-value", source="explicit")
    store.forget("nand", "plumber.old_preference")
    store.correct("nand", "plumber.preferred_area", "Koramangala")

    listed = store.inspect("nand")
    assert any(item["key"] == "plumber.preferred_area" and item["value"] == "Koramangala" for item in listed)
    assert not any(item["key"] == "plumber.old_preference" for item in listed)
    context = store.build_context(
        "nand", "plumber", "old chat " * 1000, max_bytes=200,
        trace=tmp_path / "context.jsonl",
    )
    assert len(context.encode()) <= 200
    assert "Koramangala" in context and "10:00" in context
    assert "unconfirmed-person" not in context
    assert "forgotten-value" not in context
    assert "irrelevant-" not in context
    logged = json.loads((tmp_path / "context.jsonl").read_text().splitlines()[0])
    assert logged["context"] == context
    assert logged["bytes"] <= 200


def test_confirmed_inference_can_be_used_in_new_session(tmp_path):
    db = tmp_path / "memory.sqlite"
    store = MemoryStore(db)
    store.put("nand", "global.morning", "yes", source="inferred")
    assert "morning" not in store.build_context("nand", "plumber", "", max_bytes=200)
    store.confirm("nand", "global.morning")
    resumed = MemoryStore(db)
    assert "morning: yes" in resumed.build_context("nand", "plumber", "", max_bytes=200)


def test_oversized_memory_does_not_hide_short_relevant_memory(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.put("nand", "global.a_oversized", "x" * 500, source="explicit")
    store.put("nand", "plumber.area", "Indiranagar", source="explicit")

    context = store.build_context("nand", "plumber", "", max_bytes=100)
    assert "area: Indiranagar" in context
    assert "x" * 100 not in context


def test_forgotten_fact_in_old_chat_does_not_reenter_model_context(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.put("nand", "global.technician", "Old Technician", source="explicit")
    store.forget("nand", "global.technician")
    context = store.build_context("nand", "plumber", "I once preferred Old Technician", max_bytes=200)
    assert "Old Technician" not in context


def test_service_name_cannot_expand_memory_scope_with_sql_wildcard(tmp_path):
    store = MemoryStore(tmp_path / "memory.sqlite")
    store.put("nand", "electrician.private_note", "electrician-only", source="explicit")
    context = store.build_context("nand", "%", "", max_bytes=200)
    assert "electrician-only" not in context
