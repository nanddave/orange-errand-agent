"""Drive the Orange Errand demo (runbook steps 1-9). Usage: drive.py [--record OUT.mp4] [--shots DIR]"""
import argparse, json, os, signal, subprocess, sys, time
import cdp

ap = argparse.ArgumentParser()
ap.add_argument("--record")
ap.add_argument("--shots", default="shots")
ap.add_argument("--url", default="http://127.0.0.1:8791/")
args = ap.parse_args()
os.makedirs(args.shots, exist_ok=True)

proc, pg = cdp.launch(args.url)
for attempt in range(3):
    if pg.js("location.href") == "about:blank":
        pg.call("Page.navigate", url=args.url)
    ok = False
    for _ in range(30):
        if pg.js("!!document.getElementById('f-service')"):
            ok = True
            break
        time.sleep(0.5)
    if ok:
        break
else:
    raise SystemExit("app page never loaded")
pg.call("Emulation.setFocusEmulationEnabled", enabled=True)
pg.call("Page.bringToFront")
time.sleep(1)
pg.js(cdp.CURSOR_JS)
pg.js("document.body.style.zoom='0.88'")
geo = json.loads(pg.js("JSON.stringify({sx:screenX,sy:screenY,ow:outerWidth,oh:outerHeight,iw:innerWidth,ih:innerHeight})"))
top = geo["oh"] - geo["ih"]
crop = f"crop={geo['iw']*2}:{geo['ih']*2}:{geo['sx']*2}:{(geo['sy']+top)*2}"
print("geometry", geo, crop, flush=True)

events = []
ff = None
t0 = time.time()


def mark(name, **kw):
    events.append({"t": round(time.time() - t0, 2), "event": name, **kw})
    print(events[-1], flush=True)


def shot(name):
    pg.shot(f"{args.shots}/{name}.png")


def start_recording():
    global ff, t0
    ff = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "avfoundation", "-framerate", "30",
                           "-capture_cursor", "0", "-pixel_format", "nv12", "-i", "2", "-vf", f"{crop},format=yuv420p",
                           "-c:v", "libx264", "-preset", "veryfast", "-crf", "15", "-movflags", "+faststart", args.record],
                          stdin=subprocess.PIPE)
    time.sleep(1.5)  # avfoundation warm-up; trimmed off later via the marker
    t0 = time.time()


def stop_recording():
    ff.stdin.write(b"q")
    ff.stdin.flush()
    ff.wait(timeout=60)


def rect(sel_js):
    r = pg.js(f"(()=>{{const e={sel_js};if(!e)return null;e.scrollIntoView({{block:'nearest'}});const b=e.getBoundingClientRect();return JSON.stringify([b.x+b.width/2,b.y+b.height/2])}})()")
    if r is None:
        raise RuntimeError(f"missing element {sel_js}")
    return json.loads(r)


def wait_for(cond_js, timeout=90, what=""):
    end = time.time() + timeout
    while time.time() < end:
        if pg.js(cond_js):
            return
        time.sleep(0.3)
    shot("TIMEOUT_" + what.replace(" ", "_"))
    raise RuntimeError(f"timeout waiting for {what}")


def click(sel_js, label):
    x, y = rect(sel_js)
    pg.js(f"__cur.style.transform='translate({x-4}px,{y-2}px)'")
    time.sleep(0.7)
    pg.js(f"__ripple({x},{y})")
    for typ in ("mousePressed", "mouseReleased"):
        pg.call("Input.dispatchMouseEvent", type=typ, x=x, y=y, button="left", clickCount=1)
        time.sleep(0.08)
    mark("click", what=label)
    if label in PARK:
        time.sleep(0.5)
        pg.js("__cur.style.transform='translate(1340px,450px)'")  # outside the cropped area; clears stale hover
        pg.call("Input.dispatchMouseEvent", type="mouseMoved", x=1340, y=450)


PARK = {"Find options", "Plumber 1 9:00 AM", "Yes, book it", "Show technical detail", "Hide technical detail", "Send",
        "provider cancels", "Yes, book the replacement", "Ask about reviews", "provider times out"}


def by_text(txt, tag="button"):
    return f"[...document.querySelectorAll('{tag}')].find(e=>e.offsetParent!==null&&e.innerText.includes({json.dumps(txt)}))"


def type_into(sel_id, text):
    x, y = rect(f"document.getElementById('{sel_id}')")
    pg.js(f"__cur.style.transform='translate({x-4}px,{y-2}px)'")
    time.sleep(0.6)
    for typ in ("mousePressed", "mouseReleased"):
        pg.call("Input.dispatchMouseEvent", type=typ, x=x, y=y, button="left", clickCount=1)
    pg.js(f"document.getElementById('{sel_id}').focus()")
    for i in range(1, len(text) + 1):
        # native setter + input event: same result as typing, and it does not depend on OS focus
        pg.js(f"(()=>{{const e=document.getElementById('{sel_id}');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,{json.dumps(text[:i])});e.dispatchEvent(new Event('input',{{bubbles:true}}))}})()")
        time.sleep(0.05)
    if pg.js(f"document.getElementById('{sel_id}').value") != text:
        raise RuntimeError(f"typing failed for {sel_id}")
    mark("type", into=sel_id, text=text)


def set_select(sel_id, value):
    x, y = rect(f"document.getElementById('{sel_id}')")
    pg.js(f"__cur.style.transform='translate({x-4}px,{y-2}px)'")
    time.sleep(0.6)
    pg.js(f"(()=>{{const s=document.getElementById('{sel_id}');s.value={json.dumps(value)};s.dispatchEvent(new Event('change',{{bubbles:true}}));s.dispatchEvent(new Event('input',{{bubbles:true}}))}})()")
    mark("select", into=sel_id, value=value)
    time.sleep(0.5)


def pause(s, why=""):
    time.sleep(s)
    mark("pause", seconds=s, why=why)


try:
    if args.record:
        start_recording()
    mark("START")
    # 1
    pause(6, "empty app")
    # 2 form
    type_into("f-service", "Plumber")
    type_into("f-area", "Indiranagar")
    set_select("f-day", "1")
    set_select("f-window", "mornings only")
    click("document.querySelector('[data-budget=\"1500\"]')", "budget 1,500")
    if pg.js("document.getElementById('f-budget').value") != "1500":
        pg.js("document.querySelector('[data-budget=\"1500\"]').click()")
    assert pg.js("document.getElementById('f-budget').value") == "1500", "budget preset failed"
    type_into("f-notes", "Kitchen tap is leaking")
    shot("02a-form")
    click("document.getElementById('f-go')", "Find options")
    wait_for("document.querySelectorAll('#chat button').length>=2 && /9:00 AM/.test(document.getElementById('chat').innerText)", what="option cards")
    pause(6, "option cards")
    shot("02b-options")
    # 3 reviews injection
    click("document.querySelector('#breakbox summary')", "open Try to break it")
    pause(0.8)
    click("document.querySelector('[data-break=\"reviews\"]')", "Ask about reviews")
    click("document.querySelector('#breakbox summary')", "close Try to break it")
    wait_for("document.getElementById('trace').innerText.includes('SAFETY') && (()=>{const t=document.getElementById('chat').innerText,i=t.indexOf('looks off.');return i>=0&&t.length-i>120&&!t.includes('Thinking')})()", what="SAFETY row and reply")
    pause(10, "SAFETY row")
    shot("03-safety")
    # 4 pick slot
    wait_for("[...document.querySelectorAll('#chat button')].some(e=>/9:00 AM/.test(e.innerText))", what="slot button")
    click("[...document.querySelectorAll('#chat button')].find(e=>/9:00 AM/.test(e.innerText))", "Plumber 1 9:00 AM")
    wait_for("!document.getElementById('actions').hidden", what="approval card")
    pause(10, "approval card")
    shot("04-approval")
    # 5 break: timeout
    click("document.querySelector('#breakbox summary')", "open Try to break it")
    pause(0.8)
    click("document.querySelector('[data-break=\"timeout\"]')", "provider times out")
    click("document.querySelector('#breakbox summary')", "close Try to break it")
    pause(2)
    # 6 approve
    click("document.getElementById('approve')", "Yes, book it")
    wait_for("document.getElementById('chat').innerText.includes('Booking confirmed')", what="booking confirmed")
    pause(12, "UNCERTAIN VERIFY DONE trace")
    shot("06-confirmed")
    click("document.getElementById('tech')", "Show technical detail")
    pause(10, "technical detail")
    shot("06b-technical")
    click("document.getElementById('tech')", "Hide technical detail")
    pause(1)
    # 7
    click("document.querySelector('#breakbox summary')", "open Try to break it")
    pause(0.8)
    click("document.querySelector('[data-break=\"normal\"]')", "Back to normal")
    click("document.querySelector('#breakbox summary')", "close Try to break it")
    pause(1)
    type_into("message", "give me my booking confirmation")
    click("document.querySelector('#form button')", "Send")
    wait_for("!document.getElementById('chat').innerText.includes('Working')", what="reply settle")
    pause(7, "booking confirmation reply")
    shot("07-confirmation")
    # 8
    wait_for("!document.getElementById('simbox').hidden", what="simbox")
    click("document.getElementById('simcancel')", "provider cancels")
    wait_for("!document.getElementById('actions').hidden && document.getElementById('approve').innerText.includes('replacement')", what="replacement approval")
    pause(8, "replacement offered")
    shot("08-replacement")
    # 9
    click("document.getElementById('approve')", "Yes, book the replacement")
    pause(1)
    wait_for("!document.getElementById('chat').innerText.includes('Working')", what="final settle")
    pause(9, "end")
    shot("09-end")
    mark("END")
finally:
    if ff:
        stop_recording()
    json.dump({"geometry": geo, "crop": crop, "events": events}, open(args.shots + "/events.json", "w"), indent=1)
    proc.terminate()
