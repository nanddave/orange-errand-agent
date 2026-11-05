from pathlib import Path

import pytest

from errand.evals import CASES, generate_table, run

TABLE = Path(__file__).resolve().parents[1] / "EVAL_TABLE.md"


@pytest.mark.parametrize("name,expected,_note", CASES, ids=[c[0] for c in CASES])
def test_scenario(name, expected, _note, tmp_path):
    assert run(name, tmp_path) == expected


def test_generate_table_has_row_per_case_and_no_failures(tmp_path):
    out = tmp_path / "t.md"
    assert generate_table(out) == 0
    text = out.read_text()
    assert all(f"| {name} |" in text for name, *_ in CASES) and "FAIL" not in text


def test_committed_table_is_current():
    text = TABLE.read_text()
    assert all(f"| {name} |" in text for name, *_ in CASES) and "FAIL" not in text
