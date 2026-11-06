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

## 9. Real model's slot format was rejected, so no booking could start

- **Observed:** the first live run with the real model blocked at `hold_slot` with "That slot is not in the current search results." The model had sent `2026-11-06T09:00`, and on a second run `2026-11-06 09:00`, for the offered slot `2026-11-06T09:00:00+05:30`. No offline scenario had caught it.
- **Cause:** the agent compared the model's slot to the offered slots by exact string. The scripted model always sent the exact text; a real model varies the format.
- **Fix:** match on the date and `HH:MM` parsed from whatever the model sends, then hold only the exact offered slot text. A slot the provider never offered is still blocked. The tool description also tells the model to copy the slot as listed.
- **Regression:** `tests/test_agent_behaviors.py::test_abbreviated_model_slot_is_canonicalised_but_unoffered_slot_is_blocked` and `::test_space_separated_model_slot_is_canonicalised`.

## 10–15. Defects found by an independent review of the approval changes

A separate read-only reviewer reproduced these in a throwaway copy; each was fixed with a regression test in `tests/test_review_fixes.py`.

- **Two replacement bookings for one provider cancellation.** After an expired hold was re-proposed, the cancel event's record still pointed at the old action, so a redelivered event proposed a second replacement and both could be approved. Fix: re-point the record to the new action.
- **A booked hold could be proposed again.** The hold stayed in the session after booking. Fix: drop the hold from the session once its booking is confirmed.
- **Saved times were not validated.** `remember` accepted "9:30", which sorts below every slot and silently hid all providers. Fix: require valid `HH:MM` for earliest/latest times in `remember` and `correct`.
- **Slot matching ignored the timezone offset.** `…09:00:00Z` held the `+05:30` slot. Fix: when the model gives an offset, it must equal the offered one.
- **Simulated provider cancellation could orphan a pending approval.** Fix: refuse with 409 while an action awaits approval.
- **Forgetting a preference before it was inferred did not stop the inference.** Fix: `forget` stores a tombstone row.

## 16–18. Failures found by typing real requests through the live model

Offline scenarios use a scripted model, so these only appeared with Claude in the loop. After the fixes, the same six requests all pass live.

- **Booking from the options failed.** The model sometimes called `update_request` to "pin" the chosen time, or added a free-text preferences note. Either changed the request facts, which discarded the current options, so `hold_slot` answered "slot not in the current search results" and cancel and reschedule never started. Fix: free-text preferences no longer discard options, and the prompt and tool description say never to call `update_request` just to pick an option. A real change to area, date, budget, service or time bounds still discards the options on purpose.
- **"Remember that I never want appointments before 10am" saved nothing.** The model did not know which key to use. Fix: the `memory_action` description names the keys (`global.earliest_time` as `HH:MM`, `global.preferred_area`, `<service>.technician`).
- **"Same electrician as last time" looked in saved memory instead of booking history.** Fix: the `list_bookings` description and the prompt say "last time" and "my booking" mean booking history.
- **Regression:** `tests/test_core_flow.py::test_free_text_preferences_do_not_discard_the_current_options` and `::test_tool_descriptions_steer_the_model_away_from_live_misroutes`, plus a repeat of the six live checks.

## 19. A refused changed-after-display action could never be declined

- **Observed:** found by the fault-injection fuzz. After `confirm(id, True)` was refused because the proposal changed after display, `confirm(id, False)` raised `PermissionError` too and the action stayed `proposed` forever. Nothing executed, but a user could not clear it, and the page refuses new requests while an action is pending.
- **Cause:** LangGraph replays the earlier cached resume value (`approved: True`) when the same interrupt is resumed again, so the decline never reached the ledger.
- **Fix:** a "no" on a proposed action is written to the ledger directly and never goes through the graph.
- **Regression:** `tests/test_review_fixes.py::test_a_changed_action_can_still_be_declined`.

## 20–21. Two page defects found by clicking through the demo

- **Approval buttons and message box clipped at a normal laptop window height.** At about 600 px of window height the approval card pushed past the bottom of the chat panel, so "Yes, book it" was cut off and the input was hidden. Cause: the message list had a 300 px minimum height inside a fixed-height, overflow-hidden panel. Fix: the list now has a 140 px minimum and the app a 640 px minimum height, so the card and input always fit and the list scrolls. Regression: `tests/test_web.py::test_chat_body_leaves_room_for_the_approval_card_and_input` (a layout check by string; the visual result was confirmed in the browser).
- **"Last run: waiting" after a pick or approve.** The run summary read the newest run, which had no model call, so it showed "waiting" even after a real run. Fix: it now uses the latest run that had a model call. This is page JavaScript that pytest cannot execute, so it was verified in the browser, not by a unit test.

## 22. After a booking, "is it confirmed?" showed the old options list

- **Observed:** found by the owner clicking through the live demo. After a confirmed booking, asking whether it was confirmed returned "Options: … No booking has been made." again, including the time just booked. The trace showed one model turn and no tool call.
- **Cause:** when the model answered in plain text, the agent replaced its answer with the options list whenever `state["candidates"]` was non-empty, and nothing cleared the candidates after a booking. The canned text also said "No booking has been made", which was false. Offline scenarios never asked a follow-up after completion, and the scripted model cannot ask one.
- **Fix:** a confirmed, cancelled or rescheduled action clears the search state. A text-only reply with no tool call is then answered from the provider's booking list ("Your bookings: …"), never from model prose.
- **Second path, found by the owner on the next build:** "give me my booking confirmation" made the model call `search_providers` again, which repopulated the options. Fix: booking-status questions are answered by code from the provider's booking list without calling the model. The broader cause was that every text-only reply was replaced by a canned line, so greetings and ordinary questions got "I still need…" or the options list. The model's wording now passes through for greetings, questions and clarifying asks. Code still owns options, approvals and any sentence using confirmed/booked/cancelled/rescheduled, and nothing the model says after reading provider text or after a fresh search is passed through.
- **Regression:** `tests/test_post_booking.py::test_status_question_after_booking_reports_the_booking_not_old_options`, `::test_finished_booking_clears_search_state_so_options_are_not_reshown`, and the eval row `post_booking_status_question`.

## 23. Concurrent confirms reached the provider twice

- **Observed:** found by an adversarial script, not in the demo. Twelve threads calling `confirm` on one approved action made two `book` calls (one booking, because the mock deduplicates by key). The web server's request lock hid this, but the agent API did not guarantee it.
- **Cause:** `ActionDispatcher.execute` read the status and wrote `pending` without excluding another caller in the same process.
- **Fix:** `execute` takes a per-dispatcher lock. Across processes the stored idempotency key still protects the provider.
- **Regression:** `tests/test_post_booking.py::test_concurrent_confirms_reach_the_provider_once`.

## 24. Web server: bad Content-Length, stalled body, non-string text

- **Observed:** a non-numeric `Content-Length` raised an uncaught `ValueError` (traceback, dropped connection). A client that sent headers and then stalled held the request lock, so every other request got 409. `{"text": {...}}` was accepted and turned into a string.
- **Fix:** controlled 400 for a bad length, the body is read before the lock is taken with a 10 s socket timeout, and `text` must be a string. The fuzz report's "UX" row also counted 0 checks; it now counts every agent call it makes.
- **Regression:** `tests/test_post_booking.py::test_non_numeric_content_length_is_a_400`, `::test_stalled_body_does_not_hold_the_request_lock`, `::test_chat_text_must_be_a_string`.
