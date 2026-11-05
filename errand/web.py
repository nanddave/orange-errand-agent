"""Local demo page: chat and trace side by side, with code-owned approval."""

import argparse
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from errand.agent import ChatAgent
from errand.evals import FACTS, SLOT, book_held, call
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore
from errand.model import AnthropicModel, ScriptedModel


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orange Errand Agent</title><style>
body{font:15px system-ui,sans-serif;margin:0;background:#f7f8fa;color:#17212b}header{background:#fff;border-bottom:1px solid #ddd;padding:16px 24px}h1{font-size:20px;margin:0}main{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px;height:calc(100vh - 90px);box-sizing:border-box}section{display:flex;flex-direction:column;background:white;border:1px solid #ddd;border-radius:12px;overflow:hidden}h2{font-size:15px;margin:0;padding:14px;border-bottom:1px solid #eee}.body{padding:16px;overflow:auto;flex:1;white-space:pre-wrap}.bubble{padding:10px 13px;border-radius:10px;margin:0 0 10px;max-width:90%}.you{background:#e9f1fb;margin-left:auto}.agent{background:#fff3e7}form,.actions{display:flex;gap:8px;padding:12px;border-top:1px solid #eee}input{flex:1;padding:10px;font:inherit}button{padding:10px 14px;background:#e67722;color:white;border:0;border-radius:7px;cursor:pointer}button.secondary{background:#596675}pre{font-size:12px;white-space:pre-wrap;word-break:break-word;margin:4px 0 0;padding:8px;background:#f5f6f8;border-radius:5px}.ev{white-space:normal;margin:0 0 6px;font-size:13px;word-break:break-word}.ev summary{cursor:pointer}.ev.red{color:#b42318}.ev.green{color:#067647}.ev.amber{color:#b54708}header{display:flex;justify-content:space-between;align-items:center}@media(max-width:750px){main{grid-template-columns:1fr;height:auto}section{height:45vh}}
</style><header><h1>Orange Errand Agent · local demo</h1><button id="simcancel" class="secondary" type="button">Simulate provider cancellation</button></header><main><section><h2>Chat</h2><div id="chat" class="body" aria-live="polite"></div><div id="actions" class="actions" hidden><button id="approve">Approve exact action</button><button id="deny" class="secondary">Decline</button></div><form id="form"><input id="message" aria-label="Your message" placeholder="Find a plumber this Friday morning under ₹1,500" required><button>Send</button></form></section><section><h2>Execution trace</h2><div id="trace" class="body" aria-live="polite"></div></section></main>
<script>
let pending=null;const chat=document.getElementById('chat'),trace=document.getElementById('trace'),actions=document.getElementById('actions');
function bubble(text,kind){const d=document.createElement('div');d.className='bubble '+kind;d.textContent=text;chat.append(d);chat.scrollTop=chat.scrollHeight}
async function post(path,data){const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});return r.json()}
const RED=new Set(['run_error','model_error','web_error','not_approved','status_error']),AMBER=new Set(['booking_timeout','recovery_started','cancel_503','review_flagged','provider_cancelled_event']),SKIP=new Set(['event','action_id']);
function tone(i){if(RED.has(i.event)||['blocked','error','limit_reached'].includes(i.status))return 'red';if(i.event==='approved'||i.event==='confirmed'||(i.event==='status_checked'&&i.outcome==='confirmed'))return 'green';return AMBER.has(i.event)?'amber':''}
function summary(i){const parts=[];for(const [k,v] of Object.entries(i)){if(SKIP.has(k))continue;let t=typeof v==='object'&&v!==null?JSON.stringify(v):String(v);if(t.length>80)t=t.slice(0,80)+'…';parts.push(k+'='+t)}return parts.join(' ')}
function row(i){const d=document.createElement('details');d.className='ev '+tone(i);const s=document.createElement('summary'),b=document.createElement('b'),pre=document.createElement('pre');b.textContent=i.event;s.append(b,' '+summary(i));pre.textContent=JSON.stringify(i,null,2);d.append(s,pre);return d}
async function refresh(){const r=await fetch('/trace');const items=await r.json();trace.replaceChildren(...items.map(row));trace.scrollTop=trace.scrollHeight}
function show(result){bubble(result.message||result.status,'agent');pending=result.status==='approval_required'?result.action_id:null;actions.hidden=!pending;document.getElementById('approve').textContent='Approve exact action';document.getElementById('deny').hidden=false;refresh()}
document.getElementById('form').onsubmit=async e=>{e.preventDefault();const input=document.getElementById('message'),value=input.value.trim();if(!value)return;bubble(value,'you');input.value='';show(await post('/chat',{text:value}))};
for(const [id,approved] of [['approve',true],['deny',false]])document.getElementById(id).onclick=async()=>{if(pending)show(await post('/approve',{action_id:pending,approved}))};
document.getElementById('simcancel').onclick=async()=>{const r=await fetch('/simulate-provider-cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}),d=await r.json();show(r.ok?d:{message:d.error,status:'error'})};
refresh();fetch('/pending').then(r=>r.json()).then(result=>{if(result.action_id){pending=result.action_id;actions.hidden=false;document.getElementById('approve').textContent=result.status==='pending'?'Check booking status':'Approve exact action';document.getElementById('deny').hidden=result.status==='pending';bubble(result.message,'agent')}});setInterval(refresh,3000);
</script></html>"""


def load_local_key(path: Path):
    if os.getenv("ANTHROPIC_API_KEY") or not path.exists():
        return
    for line in path.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"\'')
            return


def create_server(root: Path, *, scripted=False, scenario="timeout_success", port=8765):
    root.mkdir(parents=True, exist_ok=True)
    if not scripted:
        load_local_key(Path(__file__).resolve().parent.parent / ".env")
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Set ANTHROPIC_API_KEY in the environment or a local .env file")
    db = root / "business.sqlite"
    trace = root / "trace.jsonl"
    provider = MockBookingProvider(db, scenario=scenario, user_id="demo")
    dispatcher = ActionDispatcher(db, provider, trace)
    memory = MemoryStore(db)
    model = (ScriptedModel([call("update_request", **FACTS), call("search_providers"),
                            call("hold_slot", provider_id="plumber-1", slot=SLOT), book_held])
             if scripted else AnthropicModel())
    agent = ChatAgent(db, provider, dispatcher, memory, model)
    pending = {"action_id": None}
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id, proposal FROM actions WHERE status IN ('proposed', 'pending') ORDER BY rowid DESC").fetchall()
    pending["action_id"] = next((action_id for action_id, proposal in rows if json.loads(proposal).get("user_id") == "demo"), None)

    class Handler(BaseHTTPRequestHandler):
        def send(self, code, data, content_type="application/json"):
            body = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/":
                return self.send(200, PAGE, "text/html")
            if self.path == "/trace":
                events = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
                return self.send(200, events[-100:])
            if self.path == "/pending":
                action_id = pending["action_id"]
                if not action_id:
                    return self.send(200, {})
                proposal = dispatcher.presented_proposal(action_id)
                status = dispatcher.status(action_id)
                return self.send(200, {"action_id": action_id, "status": status,
                                       "message": f"Pending {proposal.get('action', 'booking')}: {proposal['provider_id']} at {proposal['slot']} for ₹{proposal['price']}"})
            self.send(404, {"error": "not found"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                return self.send(403, {"error": "local origin required"})
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self.send(415, {"error": "JSON required"})
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 24000:
                return self.send(413, {"error": "request too large"})
            try:
                data = json.loads(self.rfile.read(size))
                if self.path == "/chat":
                    if pending["action_id"] and dispatcher.status(pending["action_id"]) in {"proposed", "pending"}:
                        result = {"status": "approval_required", "action_id": pending["action_id"],
                                  "message": "Resolve the pending action before starting a new request."}
                    else:
                        result = agent.handle("demo", str(data["text"]))
                        pending["action_id"] = result.get("action_id")
                elif self.path == "/approve":
                    if not pending["action_id"] or data.get("action_id") != pending["action_id"] or type(data.get("approved")) is not bool:
                        return self.send(403, {"error": "no matching pending action"})
                    result = agent.confirm(pending["action_id"], data["approved"])
                    pending["action_id"] = result.get("action_id") if result.get("status") == "approval_required" else None
                elif self.path == "/simulate-provider-cancel":
                    if pending["action_id"] and dispatcher.status(pending["action_id"]) in {"proposed", "pending"}:
                        return self.send(409, {"error": "resolve the pending action first"})
                    booking =next((b for b in reversed(provider.list_bookings("demo")) if b["status"] == "confirmed"), None)
                    if booking is None:
                        return self.send(409, {"error": "no confirmed booking"})
                    provider.provider_cancel(booking["id"])
                    result = agent.on_provider_cancelled("demo", booking["id"])
                    pending["action_id"] = result.get("action_id")
                else:
                    return self.send(404, {"error": "not found"})
                self.send(200, result)
            except (KeyError, ValueError, TypeError) as error:
                self.send(400, {"error": type(error).__name__})
            except Exception as error:
                dispatcher._log("web_error", "", error=type(error).__name__)
                if pending["action_id"] and dispatcher.status(pending["action_id"]) == "pending":
                    self.send(200, {"status": "approval_required", "action_id": pending["action_id"],
                                    "message": "The provider outcome is still uncertain. Check status again; no second booking call will be made."})
                else:
                    self.send(500, {"status": "error", "message": "The request could not be completed. Check the trace."})

    return HTTPServer(("127.0.0.1", port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scripted", action="store_true", help="offline interface check")
    parser.add_argument("--scenario", default="timeout_success")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = Path.cwd() / "runtime"
    server = create_server(root, scripted=args.scripted, scenario=args.scenario, port=args.port)
    print(f"Open http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
