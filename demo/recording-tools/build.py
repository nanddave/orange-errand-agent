"""Cut raw screen capture into the final demo: captions, freezes, cards. Usage: build.py TAKE_DIR OUT.mp4"""
import json, os, re, subprocess, sys

take, out = sys.argv[1], sys.argv[2]
RAW = f"{take}/raw.mp4"
W, H = 1920, 1080
VW, VH = 1189, 920
VX, VY = (W - VW) // 2, 56
CROP = "crop=2120:1640:300:0"
HELV = "/System/Library/Fonts/HelveticaNeue.ttc"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
REG = "/System/Library/Fonts/Supplemental/Arial.ttf"
BG, ORANGE, INK, MUTED = "0x0f1218", "0xff7900", "0xf4f5f7", "0x9aa3b2"
work = f"{take}/build"
os.makedirs(work, exist_ok=True)
FF = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30"]


def textfile(name, text):
    p = f"{work}/{name}.txt"
    open(p, "w").write(text)
    return p


def dt(file, font, size, color, x, y, extra=""):
    return f"drawtext=fontfile='{font}':textfile='{file}':fontsize={size}:fontcolor={color}:x={x}:y={y}:line_spacing=10{extra}"


EV = json.load(open(f"{take}/events.json"))["events"]


def ev(kind, key=None, nth=0):
    hits = [e for e in EV if e["event"] == kind and (key is None or key in (e.get("what"), e.get("why"), e.get("into")))]
    return hits[nth]


def pstart(why):
    e = ev("pause", why)
    return e["t"] - e["seconds"]


# first visible change in the raw capture is the cursor leaving for the first field, right after the opening hold
first = subprocess.run(["ffmpeg", "-hide_banner", "-i", RAW, "-t", "15", "-vf", "select='gt(scene,0.0004)',showinfo", "-an", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
changes = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", first) if float(x) > 5]
OFFSET = changes[0] - (ev("pause", "empty app")["t"] + 0.05)
print("offset", round(OFFSET, 2), flush=True)

PRE = 0.95  # a click is logged after the cursor has travelled and pressed; cut just before that so captions match the screen
B = [0.0, ev("pause", "empty app")["t"], ev("click", "Find options")["t"] - PRE, pstart("option cards"),
     ev("click", "open Try to break it", 0)["t"] - PRE, pstart("SAFETY row"), ev("click", "Plumber 1 9:00 AM")["t"] - PRE,
     ev("click", "open Try to break it", 1)["t"] - PRE, ev("click", "Yes, book it")["t"] - PRE, ev("click", "Show technical detail")["t"] - PRE,
     ev("click", "Hide technical detail")["t"] - PRE, ev("type", "message")["t"], ev("click", "provider cancels")["t"] - PRE,
     ev("click", "Yes, book the replacement")["t"] - PRE, EV[-1]["t"]]
SPEEDS = [1, 1, 2.5, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
FREEZES = [0, 0, 0, 0, 0, 5, 4, 0, 6, 4, 0, 0, 4, 4]
CAPS = [
    "Local demo of the Orange Errand Agent. Left: the chat. Right: what the assistant is doing.\nThe model is live Claude. The providers are mock services.",
    "Step 1 · The user describes the errand: a plumber in Indiranagar, tomorrow morning, up to ₹1,500.",
    "Live Claude searches the mock providers. (Waiting on the model, shown at 2.5× speed.)",
    "Two options inside the budget and the morning window. Nothing is booked yet.",
    "Step 2 · Try to break it: ask Claude to read the provider reviews. One review hides an instruction.",
    "The guardrail flags the injected instruction and ignores it. Reviews are untrusted data.\nThe options stay and no booking is made.",
    "Step 3 · Pick Plumber 1, 9:00 AM. The agent holds the slot and asks for approval.\nNothing is booked until you click Yes.",
    "Step 4 · Break it again: the provider will time out after it has booked.",
    "Approved. The provider books, then does not answer in time. Trace: UNCERTAIN, VERIFY, DONE.\nThe agent asks the provider instead of retrying: one booking, not two.",
    "Technical detail on: every tool call, its arguments, and the approval it needed.",
    "Back to normal. The user asks for the booking confirmation.",
    "The answer comes from the provider's own booking list, written by code. No model call (see \"Last run\").",
    "Step 5 · Demo-only button: the provider cancels. The agent notices, finds a replacement,\nand asks for approval again.",
    "Approved. Replacement confirmed: Plumber 2, 9:00 AM. Every booking needed a user yes.",
]
SEGS = [(B[i], B[i + 1], SPEEDS[i], FREEZES[i], CAPS[i]) for i in range(14)]


STEPS = ["1  Describe the errand", "2  Hostile review text", "3  Approve the booking", "4  Provider times out", "5  Provider cancels"]
STEP_OF = [None, 0, 0, 0, 1, 1, 2, 3, 3, 3, 3, 3, 4, 4]


def build_seg(i, a, b, speed, freeze, cap):
    cf = textfile(f"cap{i}", cap)
    title = textfile("title", "ORANGE ERRAND AGENT")
    badge = textfile("badge", "LIVE CLAUDE MODEL  ·  MOCK PROVIDERS  ·  LOCAL APP")
    lhead = textfile("lhead", "THE FIVE STEPS")
    rhead = textfile("rhead", "WHO DOES WHAT")
    legend = [("LIVE", "Claude: the conversation\nand every tool choice", ORANGE), ("MOCK", "Providers and the faults\nthe buttons inject", MUTED),
              ("CODE", "Approval, idempotency and\nevery claim about a booking", INK)]
    vf = (f"setpts=(PTS-STARTPTS)/{speed},{CROP},scale={VW}:{VH}:flags=lanczos"
          + (f",tpad=stop_mode=clone:stop_duration={freeze}" if freeze else "")
          + f",pad={W}:{H}:{VX}:{VY}:color={BG}"
          + f",drawbox=x={VX-2}:y={VY-2}:w={VW+4}:h={VH+4}:color=0x2a2f3a:t=2"
          + f",drawbox=x=0:y={VY-2}:w={W}:h=2:color={ORANGE}@0.9:t=fill"
          + "," + dt(title, BOLD, 24, INK, 48, 16)
          + "," + dt(badge, BOLD, 22, ORANGE, "w-48-text_w", 18)
          + "," + dt(lhead, BOLD, 18, MUTED, 48, 130)
          + "," + dt(rhead, BOLD, 18, MUTED, f"{VX+VW+28}", 130)
          + "," + dt(cf, HELV, 31, INK, "(w-text_w)/2", f"{VY+VH}+(({H}-{VY+VH})-text_h)/2"))
    for k, name in enumerate(STEPS):
        f = textfile(f"step{i}_{k}", name)
        on = STEP_OF[i] == k
        vf += "," + dt(f, BOLD if on else REG, 24, ORANGE if on else MUTED, 48, 175 + k * 62)
        if on:
            vf += f",drawbox=x=30:y={170 + k * 62}:w=5:h=34:color={ORANGE}:t=fill"
    for k, (tag, body, col) in enumerate(legend):
        y = 175 + k * 150
        vf += "," + dt(textfile(f"lt{i}_{k}", tag), BOLD, 24, col, f"{VX+VW+28}", y)
        vf += "," + dt(textfile(f"lb{i}_{k}", body), HELV, 21, INK, f"{VX+VW+28}", y + 36)
    path = f"{work}/seg{i:02d}.mp4"
    start = a + OFFSET
    subprocess.run(FF + ["-ss", f"{start:.3f}", "-t", f"{(b - a):.3f}", "-i", RAW, "-vf", vf, "-an"] + ENC + [path], check=True)
    return path


def card(name, secs, lines, tag=None):
    """lines: (text, size, bold, color, y)."""
    vf = [f"drawbox=x=0:y=0:w={W}:h=6:color={ORANGE}:t=fill"]
    for k, (text, size, bold, color, y) in enumerate(lines):
        f = textfile(f"{name}{k}", text)
        vf.append(dt(f, BOLD if bold else REG, size, color, 160, y))
    vf.append(f"fade=t=in:st=0:d=0.5,fade=t=out:st={secs-0.5}:d=0.5")
    path = f"{work}/{name}.mp4"
    subprocess.run(FF + ["-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d={secs}:r=30", "-vf", ",".join(vf), "-an"] + ENC + [path], check=True)
    return path


facts = json.load(open(f"{take}/facts.json"))
cards_pre = [
    card("c1", 7, [("Orange Errand Agent", 96, True, INK, 400), ("A booking assistant that never books without your approval.", 40, False, MUTED, 530),
                   ("Orange engineering assignment  ·  demo  ·  Nand Dave", 30, False, ORANGE, 640)]),
    card("c2", 18, [("What you are about to see", 62, True, INK, 230),
                    ("Chat on the left, the assistant's trace on the right.", 36, False, MUTED, 340),
                    ("•  Claude is live: the conversation and tool choices are real model calls.", 36, False, INK, 470),
                    ("•  The providers are mock services with scripted faults. No real provider or payment.", 36, False, INK, 550),
                    ("•  Code, not the model, owns approval, idempotency and every claim about a booking.", 36, False, INK, 630),
                    ("•  \"Try to break it\" buttons inject faults so you can watch the guardrails work.", 36, False, INK, 710)]),
]
segs = [build_seg(i, *s) for i, s in enumerate(SEGS)]
cards_post = [
    card("c3", 20, [("What the demo showed", 62, True, INK, 200),
                    ("1  Untrusted provider text cannot change the request or skip approval.", 36, False, INK, 340),
                    ("2  A booking timeout is verified with the provider, not retried: one booking.", 36, False, INK, 420),
                    ("3  Booking status is answered by code from the provider's own booking list.", 36, False, INK, 500),
                    ("Evidence in the repository", 40, True, ORANGE, 640),
                    (facts["line1"], 34, False, INK, 720), (facts["line2"], 34, False, INK, 780)]),
    card("c4", 10, [("Live model vs scripted", 56, True, INK, 330),
                   ("Live: Claude's conversation, tool calls and replies in this recording.", 36, False, INK, 450),
                   ("Scripted: the mock providers and the faults injected by the \"Try to break it\" buttons.", 36, False, INK, 520),
                   ("Run it yourself: README.md  →  ./proof.sh", 34, True, ORANGE, 650)]),
]
parts = cards_pre + segs + cards_post
lst = f"{work}/list.txt"
open(lst, "w").write("".join(f"file '{os.path.abspath(p)}'\n" for p in parts))
subprocess.run(FF + ["-f", "concat", "-safe", "0", "-i", lst, "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-shortest",
                     "-c:v", "copy", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out], check=True)
print(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "csv=p=0", out], capture_output=True, text=True).stdout)
