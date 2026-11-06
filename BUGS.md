# Reproduced bugs and regression coverage

Only reproduced issues are recorded here. Each fix has a named regression test; the full suite also runs the offline evaluation and seeded fault-injection checks.

## 1. First cancellation retry never completed

- **Observed:** the second cancellation attempt returned another simulated 503 instead of completing.
- **Cause:** the mock wrote its first-attempt marker in the same transaction that was rolled back when the simulated failure was raised.
- **Fix:** commit the marker before raising the transient failure.
- **Regression:** `tests/test_mock_contract.py::test_cancel_reschedule_and_list_bookings`.

## 2. Approval could apply to details the user had not seen

- **Observed:** changing a proposal's price after display still let the original confirmation book it.
- **Cause:** confirmation loaded the current proposal rather than proving it matched the presented proposal.
- **Fix:** persist the presented proposal and require it, the current proposal, and the approved payload to match.
- **Regression:** `tests/test_chat.py::test_changed_proposal_after_display_cannot_use_old_approval_click`.

## 3. Real-model slot formatting prevented booking

- **Observed:** a live model supplied `2026-11-06T09:00` or `2026-11-06 09:00` for an offered `2026-11-06T09:00:00+05:30` slot, which was rejected.
- **Cause:** slot comparison required byte-for-byte equality.
- **Fix:** canonicalise date and time to the exact offered slot while preserving an explicitly supplied timezone check.
- **Regression:** `tests/test_agent_behaviors.py::test_abbreviated_model_slot_is_canonicalised_but_unoffered_slot_is_blocked` and `::test_space_separated_model_slot_is_canonicalised`.

## 4. Review follow-up lost the current options

- **Observed:** after reading provider reviews, the assistant gave a generic response and the user could no longer select the listed provider.
- **Cause:** free-form text from a provider-influenced run was correctly suppressed, but the safe fallback omitted the existing choices.
- **Fix:** produce a code-owned review summary and return the unchanged options.
- **Regression:** `tests/test_agent_behaviors.py::test_review_question_keeps_options_and_answers_from_code`.

## 5. Ordinary chat answers were too canned

- **Observed:** date ranges, remembered time preferences, comparison questions, and typed confirmation did not reliably follow the user’s request.
- **Cause:** scripted tests covered tool safety but not normal free-form follow-ups and model formatting variation.
- **Fix:** add bounded date-range parsing, explicit typed approval handling, memory-aware time filtering, grounded comparison responses, and `tests/live_probe.py` for optional paid live checks.
- **Regression:** `tests/test_agent_behaviors.py::test_this_week_range_is_accepted_and_offers_the_first_days`, `::test_remembered_time_of_day_shapes_the_next_search`, `tests/test_web.py::test_typed_yes_and_no_decide_the_pending_proposal_and_other_text_does_not`.
