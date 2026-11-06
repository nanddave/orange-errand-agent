#!/usr/bin/env python3
"""Convert a Claude Code or Codex session JSONL into Markdown. Standard library only.

Honesty rules: wording of prompts and replies is never changed. Anything removed is
replaced by a visible `[REDACTED: reason]` or `[... N lines omitted]` marker, and every
output ends with counts of both. Usage:
    build_transcript.py SESSION.jsonl OUT.md [--label "Claude acct A"] [--raw-out raw/X.jsonl]
    build_transcript.py --selftest
"""
import json, re, sys, argparse
from collections import Counter
from datetime import datetime, timedelta, timezone

OWN_EMAIL = "nand@brokailabs.com"  # kept for attribution; every other address is redacted
DUBAI = timezone(timedelta(hours=4))
MAX_RESULT_LINES, MAX_RESULT_CHARS, HEAD_LINES = 30, 3000, 12

# (type, regex, replacement-reason). Order matters: specific before generic.
PATTERNS = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), "Anthropic API key"),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), "API key (sk-...)"),
    ("other_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|xox[abpr]-[A-Za-z0-9\-]{10,}|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,})"), "auth token"),
    ("bearer_token", re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._\-]{16,}"), "bearer token"),
    ("env_secret_line", re.compile(r"(?<![\w])([A-Za-z0-9_]*(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD)[A-Za-z0-9_]*[ \t]*=)[ \t]*[^\s\"'`]\S*", re.I), "secret value in KEY=value line"),
    ("email", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}"), "email address"),
    ("phone", re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s\-]?\d[\d\s\-]{7,12}\d|\b\d{3}[\-.\s]\d{3}[\-.\s]\d{4}\b)(?![\w.])"), "phone number"),
    ("unrelated_path", re.compile(r"/Users/[^/\s]+/Downloads/(?!Orange_Engineering_Assignment|Job_FInder/orange-errand-agent|Job_FInder(?![\w/]))(?:Job_FInder/(?!orange-errand-agent)[^\s\"'`)\]>,;]+|(?:Flower Business|Content Studio|Tech Monkey)(?:/[^\s\"'`)\]>,;]*)?|[^\s/\"'`)\]]+)"), "path of an unrelated project"),
    ("unrelated_path", re.compile(r"/Users/[^/\s]+/\.claude(?:-instances)?/[^\s\"'`)\]>,;]+"), "path in assistant-tool configuration"),
]
ENV_TARGET = re.compile(r"""(?<!--exclude)(?<!--exclude=)(?:^|[\s/"'])\.env(?![\w.-])""")
INJECTED_PREFIXES = ("Base directory for this skill", "<system-reminder", "# AGENTS.md", "<environment_context", "<user_instructions", "<permissions", "<task-notification")


class Counts:
    def __init__(self):
        self.red, self.omit = Counter(), Counter()

    def redact(self, text):
        for kind, rx, why in PATTERNS:
            def sub(m, kind=kind, why=why):
                if kind == "email" and m.group(0).lower() == OWN_EMAIL:
                    return m.group(0)
                self.red[kind] += 1
                if kind == "bearer_token":
                    return f"{m.group(1)} [REDACTED: {why}]"
                if kind == "env_secret_line":
                    return f"{m.group(1)} [REDACTED: {why}]"
                return f"[REDACTED: {why}]"
            text = rx.sub(sub, text)
        return text

    def whole(self, why, kind="env_file"):
        self.red[kind] += 1
        return f"[REDACTED: {why}]"

    def trunc(self, text, what="lines"):
        lines = text.split("\n")
        if len(lines) > MAX_RESULT_LINES or len(text) > MAX_RESULT_CHARS:
            keep = lines[:HEAD_LINES]
            while len("\n".join(keep)) > MAX_RESULT_CHARS and len(keep) > 1:
                keep.pop()
            n = len(lines) - len(keep)
            self.omit["long_tool_result"] += 1
            return "\n".join(keep) + f"\n[… {n} {what} omitted]"
        return text


def ts(raw):
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(DUBAI).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw or "?"


def one_line(inp, limit=220):
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except Exception:
            return inp[:limit]
    if not isinstance(inp, dict):
        return str(inp)[:limit]
    for k in ("command", "cmd", "file_path", "path", "pattern", "url", "query", "description", "prompt"):
        if k in inp:
            s = " ".join(str(inp[k]).split())
            return (s[:limit] + "…") if len(s) > limit else s
    s = " ".join(json.dumps(inp).split())
    return (s[:limit] + "…") if len(s) > limit else s


def flat(content):
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict):
            out.append(b.get("text") or (flat(b["content"]) if "content" in b else "") or (f"[{b.get('type')} block]" if b.get("type") not in ("text", None) else ""))
    return "\n".join(x for x in out if x)


def events(path):
    """Yield (timestamp, role, kind, payload, call_id, tool_name) in file order."""
    first = None
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    codex = any(r.get("type") == "session_meta" for r in rows[:3])
    for r in rows:
        t = r.get("timestamp", "")
        if codex:
            p = r.get("payload", {})
            if r.get("type") != "response_item":
                continue
            pt = p.get("type")
            if pt == "message":
                yield t, p.get("role", "?"), "text", flat(p.get("content")), None, None
            elif pt in ("function_call", "custom_tool_call"):
                yield t, "assistant", "call", p.get("arguments", p.get("input", "")), p.get("call_id"), p.get("name")
            elif pt in ("function_call_output", "custom_tool_call_output"):
                o = p.get("output", "")
                if isinstance(o, dict):
                    o = o.get("content") or json.dumps(o)
                yield t, "tool", "result", o if isinstance(o, str) else json.dumps(o), p.get("call_id"), None
            elif pt == "reasoning":
                yield t, "assistant", "thinking", "", None, None
        else:
            ty = r.get("type")
            if ty not in ("user", "assistant"):
                continue
            side = " (subagent)" if r.get("isSidechain") else ""
            c = r.get("message", {}).get("content")
            if isinstance(c, str):
                yield t, ty + side, "text", c, None, None
                continue
            for b in c or []:
                bt = b.get("type")
                if bt == "text":
                    yield t, ty + side, "text", b.get("text", ""), None, None
                elif bt == "thinking":
                    yield t, ty + side, "thinking", b.get("thinking", ""), None, None
                elif bt == "tool_use":
                    yield t, "assistant" + side, "call", b.get("input", {}), b.get("id"), b.get("name")
                elif bt == "tool_result":
                    yield t, "tool", "result", flat(b.get("content")), b.get("tool_use_id"), None
                elif bt:
                    yield t, ty + side, "text", f"[{bt} block]", None, None


def render(path, label=""):
    cnt = Counts()
    names, env_calls = {}, set()
    out = [f"# Session transcript: {label or path.split('/')[-1]}", "",
           f"Source file: `{path.split('/')[-1]}`. Times are Dubai (UTC+4). Tool calls are one-line summaries; "
           "long tool results are truncated with a visible marker; nothing is removed without a `[REDACTED: …]` or `[… N lines omitted]` marker.", ""]
    n = 0
    for t, role, kind, payload, cid, tname in events(path):
        if kind == "thinking":
            cnt.omit["thinking_block"] += 1
            body, who = "[… reasoning block omitted (not part of the visible conversation)]", "Assistant"
        elif kind == "call":
            names[cid] = tname
            s = one_line(payload)
            target = " ".join(str(payload.get(k, "")) for k in ("command", "cmd", "file_path", "path")) if isinstance(payload, dict) else str(payload)
            if ENV_TARGET.search(target):
                env_calls.add(cid)
            body, who = f"`{cnt.redact(s)}`", f"Tool call: {tname}"
        elif kind == "result":
            who = f"Tool result: {names.get(cid) or 'tool'}"
            if cid in env_calls:
                body = cnt.whole("output of reading an .env file")
            else:
                body = "```\n" + cnt.trunc(cnt.redact(payload or "")) + "\n```"
        else:
            who = {"user": "You", "assistant": "Assistant"}.get(role.split(" ")[0], role.capitalize()) + (" (subagent)" if "subagent" in role else "")
            if role.startswith("developer") or (role.startswith("user") and payload.lstrip().startswith(INJECTED_PREFIXES)):
                first_line = cnt.redact(payload.strip().split("\n")[0][:100])
                cnt.omit["injected_context"] += 1
                body = f"[… {len(payload.splitlines())} lines omitted: injected harness/skill context, not typed by the user. Starts: “{first_line}”]"
                who = "Injected context"
            else:
                body = cnt.redact(payload)
        n += 1
        out += [f'<a id="t{n:04d}"></a>', f"### t{n:04d} · {ts(t)} · {who}", "", body, ""]
    red, om = sum(cnt.red.values()), sum(cnt.omit.values())
    out += ["---", "## Redaction and omission report", "",
            f"- Turns rendered: {n}",
            f"- Redactions: {red}" + (" (" + ", ".join(f"{k}: {v}" for k, v in sorted(cnt.red.items())) + ")" if red else ""),
            f"- Omissions: {om}" + (" (" + ", ".join(f"{k}: {v}" for k, v in sorted(cnt.omit.items())) + ")" if om else ""), ""]
    return "\n".join(out), cnt


def redact_raw(src, dst):
    """Raw copy: same text-level redaction, wording untouched otherwise. Returns Counts."""
    cnt = Counts()
    with open(src, encoding="utf-8") as f, open(dst, "w", encoding="utf-8") as g:
        for line in f:
            g.write(cnt.redact(line))
    return cnt


def selftest():
    c = Counts()
    fake = "sk-ant-" + "api03-" + "A" * 30
    s = c.redact(f"key {fake} and ANTHROPIC_API_KEY=abc123 mail a@b.com me {OWN_EMAIL} call +971 50 123 4567 /Users/x/Downloads/Flower Business/y.txt")
    assert fake not in s and "abc123" not in s and "a@b.com" not in s and OWN_EMAIL in s
    assert "4567" not in s and "Flower Business" not in s, s
    assert c.redact("/Users/nanddave/Downloads/Job_FInder/orange-errand-agent/errand/a.py").endswith("a.py")
    assert "[REDACTED" not in c.redact("/Users/nanddave/Downloads/Orange_Engineering_Assignment.pdf")
    assert "built-in-motion" not in c.redact("/Users/nanddave/Downloads/Job_FInder/built-in-motion/x")
    assert "omitted]" in c.trunc("\n".join(["l"] * 100))
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
        sys.exit(0)
    ap = argparse.ArgumentParser()
    ap.add_argument("src"); ap.add_argument("out"); ap.add_argument("--label", default=""); ap.add_argument("--raw-out")
    a = ap.parse_args()
    md, cnt = render(a.src, a.label)
    open(a.out, "w", encoding="utf-8").write(md)
    print(a.out, "redactions", dict(cnt.red), "omissions", dict(cnt.omit))
    if a.raw_out:
        rc = redact_raw(a.src, a.raw_out)
        print(a.raw_out, "raw redactions", dict(rc.red))
