# Real bugs encountered while building

Record an issue here only after it is reproduced. For each: observed failure, root cause, fix, and regression test. The assignment asks for three real bugs; do not invent entries to reach that count.

## 1. Cancel's first 503 repeated forever

- **Observed:** `test_cancel_reschedule_and_list_bookings` failed on its second `cancel` call: it received another 503 instead of cancelling.
- **Cause:** the mock inserted the first-attempt marker inside a SQLite transaction, then raised the simulated 503 before leaving the connection context. The exception rolled back the marker.
- **Fix:** commit the marker first, then raise the 503 outside the transaction. The second call now sees the marker and completes cancellation.
- **Regression:** `tests/test_mock_contract.py::test_cancel_reschedule_and_list_bookings`.

## 2. Pending booking could be relabelled as a different provider

- **Observed:** `test_pending_action_cannot_be_changed_to_different_provider` showed that `change()` accepted a new provider after the original booking timed out and its status lookup failed.
- **Cause:** the update changed the proposal and cleared approval without checking action status. A later status lookup could confirm the *old* booking while the action displayed the *new* provider.
- **Fix:** allow proposal edits only while the action remains `proposed`. A pending or completed action must be reconciled or replaced with a new action ID.
- **Regression:** `tests/test_actions.py::test_pending_action_cannot_be_changed_to_different_provider` (and the completed reschedule case).

## 3. Oversized memory hid a later relevant preference

- **Observed:** `test_oversized_memory_does_not_hide_short_relevant_memory` returned empty context even though a short plumber-area preference fit the budget.
- **Cause:** context assembly stopped at the first memory too large to fit. An earlier oversized global fact prevented later relevant memories from being considered.
- **Fix:** skip that one memory and continue evaluating the rest under the same byte budget.
- **Regression:** `tests/test_memory.py::test_oversized_memory_does_not_hide_short_relevant_memory`.

## 4. Forgotten preference leaked through old chat

- **Observed:** `test_forgotten_fact_in_old_chat_does_not_reenter_model_context` failed: the forgotten technician name still appeared in the assembled context through a previous chat message.
- **Cause:** filtering the memory table was insufficient because context assembly also appended raw prior conversation.
- **Fix:** send current structured facts and selected confirmed memories only; do not replay old chat into model context.
- **Regression:** `tests/test_memory.py::test_forgotten_fact_in_old_chat_does_not_reenter_model_context`.

## 5. Service wildcard exposed unrelated memories

- **Observed:** `test_service_name_cannot_expand_memory_scope_with_sql_wildcard` failed when service `%` selected an electrician-only preference.
- **Cause:** the service name was interpolated into a SQL `LIKE` pattern, where `%` is a wildcard.
- **Fix:** select confirmed, current memories for the user and apply literal service-prefix matching in Python.
- **Regression:** `tests/test_memory.py::test_service_name_cannot_expand_memory_scope_with_sql_wildcard`.

## 6. Approval click could authorize a changed proposal

- **Observed:** `test_changed_proposal_after_display_cannot_use_old_approval_click` failed: changing the price after display still let `confirm()` book.
- **Cause:** `confirm()` loaded the latest proposal and sent it as the user's approval, even though the user had seen the original details.
- **Fix:** persist the presented proposal separately and require it to match both the current proposal and the confirmation payload. A material change needs a new presented action.
- **Regression:** `tests/test_chat.py::test_changed_proposal_after_display_cannot_use_old_approval_click`; three offline eval rows also cover price, provider, and slot.

## 7. Area correction reused stale search results

- **Observed:** `test_area_correction_invalidates_old_search_results` failed because a hold for an Indiranagar search remained available after the user changed the area to Koramangala.
- **Cause:** updating structured facts did not clear cached candidates and holds from the previous request.
- **Fix:** invalidate candidates and holds whenever a request fact changes; recheck budget and time before proposing a booking.
- **Regression:** `tests/test_chat.py::test_area_correction_invalidates_old_search_results`.

## 8. Proactive replacement ignored active time preference

- **Observed:** `test_replacement_respects_active_not_before_time` selected a 09:00 replacement despite the active request saying not before 10:00.
- **Cause:** the cancellation handler checked only a global memory preference, bypassing the normal slot filter.
- **Fix:** use the same time-constraint check as the search and hold path.
- **Regression:** `tests/test_proactive.py::test_replacement_respects_active_not_before_time`.

