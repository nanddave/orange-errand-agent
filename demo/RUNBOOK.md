# Demo recording runbook (about 4 minutes)

Server: `http://127.0.0.1:8790/` (fresh database, live model, bookings succeed until you press a break-it button). If it is not running:

```sh
mkdir -p /tmp/orange-demo && cd /tmp/orange-demo && rm -rf runtime && PYTHONPATH=/Users/nanddave/Downloads/Job_FInder/orange-errand-agent /Users/nanddave/Downloads/Job_FInder/orange-errand-agent/.venv/bin/python -m errand.web --scenario success --port 8790
```

## Set up the screen (2 minutes, once)

1. Turn on Do Not Disturb (no notification banners).
2. Open a NEW Chrome window with only this one tab. Hide the bookmarks bar (Cmd+Shift+B). Press Cmd+0 so the page is at 100% and make the window at least 1200 px wide so chat and trace sit side by side.
3. Close every other window you can see. Press Cmd+Shift+5, choose "Record Selected Portion", and drag the box over **only the page area** (not the tab strip or the address bar).
4. Do not start recording until you are ready to follow the steps below without stopping. Narration is optional; I add captions afterwards.

## Steps (leave a 3 second pause after each marked "pause")

1. Page loads with the empty app. **Pause 4 s.**
2. Fill the form: Plumber, Indiranagar, Tomorrow, Mornings only, budget ₹1,500 (use the preset button), details "Kitchen tap is leaking". Click **Find options**. Wait for the option cards. **Pause 3 s.**
3. Open **Try to break it** and click **Ask about reviews**. Wait for the reply. In the right panel a "SAFETY" row says a risky review was flagged. **Pause 5 s** on that row.
4. Click **Plumber 1, 9:00 AM**. The approval card appears (provider, time, price, "Nothing is booked until you click Yes."). **Pause 5 s.**
5. Open **Try to break it** again and click **Next booking: provider times out after booking**. **Pause 2 s.**
6. Click **Yes, book it**. Wait. The chat shows the green "Booking confirmed" card with the line about the provider not answering in time. In the trace see UNCERTAIN, VERIFY, DONE. **Pause 8 s** on the trace.
7. Click **Back to normal**. Type `give me my booking confirmation` and press Send. The reply lists your booking. **Pause 4 s.**
8. Click **Pretend the provider cancels my booking** (top right). The approval card offers a replacement. **Pause 4 s.**
9. Click **Yes, book the replacement**. **Pause 4 s**, then stop recording (Cmd+Ctrl+Esc or the stop icon in the menu bar).

Rules for me afterwards: I will not change what the screen shows. I add captions, a title card and the evidence card, and trim dead time. Which parts are live-model and which are scripted will be stated in the captions.

If a step goes wrong (slow reply, error), keep going or press stop and start again from step 1 after running the command above to reset the database. A failure on camera is fine to keep if the app handles it; tell me and I will caption it truthfully.
