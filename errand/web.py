"""Local demo page: chat and trace side by side, with code-owned approval."""

import argparse
import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from errand.agent import ChatAgent, _slot_label
from errand.evals import FACTS, SLOT, book_held, call
from errand.gate import ActionDispatcher, MockBookingProvider
from errand.memory import MemoryStore
from errand.model import AnthropicModel, RecordingModel, ReplayModel, ScriptedModel


PAGE = r"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Orange Errand Agent</title><style>
body{font:15px system-ui,sans-serif;margin:0;background:#f7f8fa;color:#17212b}header{background:#fff;border-bottom:1px solid #ddd;padding:16px 24px}h1{font-size:20px;margin:0}main{display:grid;grid-template-columns:1fr 1fr;gap:16px;padding:16px;height:calc(100vh - 150px);box-sizing:border-box}section{display:flex;flex-direction:column;background:white;border:1px solid #ddd;border-radius:12px;overflow:hidden;min-height:0}h2{font-size:15px;margin:0;padding:14px;border-bottom:1px solid #eee}.body{padding:16px;overflow:auto;flex:1;white-space:pre-wrap}.bubble{padding:10px 13px;border-radius:10px;margin:0 0 10px;max-width:90%}.you{background:#e9f1fb;margin-left:auto}.agent{background:#fff3e7}[hidden]{display:none!important}form,.actions{display:flex;gap:8px;padding:12px;border-top:1px solid #eee}.actions{flex-wrap:wrap}#proposal{width:100%;white-space:pre-wrap}input[type=text]{flex:1;padding:10px;font:inherit}button{padding:10px 14px;background:#e67722;color:white;border:0;border-radius:7px;cursor:pointer}button.secondary{background:#596675}button:disabled{opacity:.5;cursor:default}pre{font-size:12px;white-space:pre-wrap;word-break:break-word;margin:4px 0 0;padding:8px;background:#f5f6f8;border-radius:5px}.ev{white-space:normal;margin:0 0 6px;font-size:13px;word-break:break-word}.ev summary{cursor:pointer}.ev.red{color:#b42318}.ev.green{color:#067647}.ev.amber{color:#b54708}.tag{font-weight:700;font-size:11px;margin-right:6px}.how{background:#fff3e7;padding:8px 24px;font-size:13px;border-bottom:1px solid #eee}.legend{font-size:12px;color:#596675;padding:8px 14px;border-bottom:1px solid #eee}.choices{white-space:normal;margin:0 0 10px}.choices .card{border:1px solid #ddd;border-radius:8px;padding:8px;margin:0 0 8px}.choices button{margin:4px 6px 0 0}.top{display:flex;justify-content:space-between;align-items:center}.cap{font-size:12px;color:#596675;margin:4px 0 0}@media(max-width:750px){main{grid-template-columns:1fr;height:auto}section{height:45vh}}
</style><header><div class="top"><h1>Orange Errand Agent · local demo</h1><div id="simbox" hidden><button id="simcancel" class="secondary" type="button">Pretend the provider cancels my booking</button><div class="cap">Demo only: pretends your provider cancelled, so you can watch the assistant react without being asked.</div></div></div></header><div class="how">How this works: 1 Tell me what you need · 2 Pick a provider and time · 3 You approve · 4 Done. I never book without your yes.</div><main><section><h2>Chat</h2><div id="chat" class="body" aria-live="polite"></div><div id="actions" class="actions" hidden><div id="proposal"></div><button id="approve">Yes, book it</button><button id="deny" class="secondary">No, don't</button></div><form id="form"><input type="text" id="message" aria-label="Your message" placeholder="Find a plumber this Friday morning under ₹1,500" required><button>Send</button></form></section><section><h2>What the assistant is doing</h2><div class="legend">SAFETY = a guardrail acted · APPROVAL = needs your yes · VERIFY = checked with the provider · UNCERTAIN = outcome not known yet<br><label><input type="checkbox" id="tech"> Show technical detail</label></div><div id="runsum" class="legend">Last run: waiting</div><div id="trace" class="body" aria-live="polite"></div></section></main>
<script>
let pending=null;const chat=document.getElementById('chat'),trace=document.getElementById('trace'),actions=document.getElementById('actions'),tech=document.getElementById('tech');
const DAYS=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'],MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function readableTimes(s){return String(s).replace(/(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g,(m,y,mo,d,h,mi)=>{const hh=+h;return DAYS[new Date(Date.UTC(+y,+mo-1,+d)).getUTCDay()]+' '+(+d)+' '+MONTHS[+mo-1]+', '+(hh%12||12)+':'+mi+' '+(hh<12?'AM':'PM')})}
function bubble(text,kind){const d=document.createElement('div');d.className='bubble '+kind;d.textContent=kind==='agent'?readableTimes(text):text;chat.append(d);chat.scrollTop=chat.scrollHeight;return d}
let timer=null;function poll(ms){clearInterval(timer);timer=setInterval(refresh,ms)}
function setBusy(on){for(const n of document.querySelectorAll('button,#message'))n.disabled=on}
async function run(path,data,label){setBusy(true);poll(1000);const wait=bubble(label,'agent');let out;try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});out=await r.json()}catch(e){out={message:'Could not reach the assistant. Please try again.',status:'error'}}finally{wait.remove();setBusy(false);poll(3000)}show(out)}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function clearChoices(){for(const n of chat.querySelectorAll('.choices'))n.remove()}
function chipBox(){clearChoices();const d=el('div','','choices');chat.append(d);return d}
function chip(box,label,onclick){const b=el('button',label,'secondary');b.type='button';b.onclick=()=>{clearChoices();onclick()};box.append(b)}
function send(text){bubble(text,'you');return run('/chat',{text},'Thinking…')}
const STARTS=['Plumber for a leaking tap, tomorrow morning, under ₹1,500 in Indiranagar','Electrician to fix a tripping switchboard, today evening, under ₹2,000 in Koramangala','AC servicing for 2 split ACs, tomorrow, under ₹2,000 in JP Nagar'];
const MISSING={area:['Area',[['Indiranagar','Indiranagar'],['Koramangala','Koramangala'],['JP Nagar','JP Nagar']]],date_range:['Date',[['Today','today'],['Tomorrow','tomorrow']]],budget:['Budget',[['₹1,000','₹1,000'],['₹1,500','₹1,500'],['₹2,000','₹2,000']]],service:['Service',[['Plumber','plumber'],['Electrician','electrician'],['AC servicing','AC servicing']]]};
function startChips(){const box=chipBox();for(const t of STARTS)chip(box,t,()=>send(t))}
function missingChips(missing){const box=chipBox();for(const key of missing){const m=MISSING[key==='date'?'date_range':key];if(!m)continue;for(const [label,value] of m[1])chip(box,label,()=>send(m[0]+': '+value))}}
function optionCards(options){const box=chipBox();for(const o of options){const card=el('div','','card');card.append(el('b',o.name),el('div','Rating '+o.rating+' · ₹'+o.price));for(const s of o.slots){const b=el('button',s.label);b.type='button';b.onclick=()=>{clearChoices();run('/select',{provider_id:o.provider_id,slot:s.value},'Thinking…')};card.append(b)}box.append(card)}}
const RED=new Set(['run_error','model_error','web_error','not_approved','status_error']),SKIP=new Set(['event','action_id']);
const T=(tag,text,tone)=>({tag,text,tone:tone||''});
function plain(i){const a=i.arguments||{},r=i.result;switch(i.event){
case 'tool_call':if(i.tool==='update_request'){const p=[a.service,a.area,a.date_range,a.budget?'up to ₹'+a.budget:null].filter(Boolean);return T('AI','Understood your request: '+p.join(' · '))}if(i.tool==='get_provider')return T('PROVIDER','Read reviews of '+a.provider_id);return null;
case 'tool_result':if(i.tool==='search_providers')return T('PROVIDER','Searched and found '+(Array.isArray(r)?r.length:0)+' providers');if(i.tool==='hold_slot')return T('PROVIDER','Held the slot for 10 minutes at ₹'+(r&&r.price));return null;
case 'review_flagged':return T('SAFETY',"Flagged a risky review and ignored it: '"+i.excerpt+"'",'amber');
case 'select_clicked':return T('YOU','You picked '+i.provider_id+' at '+readableTimes(i.slot));
case 'proposed':return null;
case 'approved':return T('YOU','You approved','green');
case 'book_call':return T('PROVIDER','Sent the booking request');
case 'booking_timeout':return T('UNCERTAIN',"The provider didn't answer in time, so the outcome is unknown",'amber');
case 'status_checked':return T('VERIFY','Asked the provider: '+(i.outcome==='confirmed'?'confirmed':'not found'));
case 'confirmed':return T('DONE','Confirmed — one booking','green');
case 'hold_reproposed':return T('APPROVAL','Hold expired: new hold, new approval needed');
case 'memory_inferred':return T('MEMORY','Noticed a possible preference (not used until you confirm)');
case 'provider_cancelled_event':return T('PROVIDER','Provider cancelled the booking','amber');
case 'run_outcome':if(i.status==='approval_required')return T('APPROVAL','Asked for your approval');if(['blocked','error','limit_reached'].includes(i.status))return T('ERROR',i.message||i.status,'red');return null;
default:if(RED.has(i.event))return T('ERROR',i.message||i.error||i.event,'red');return null}}
function summary(i){const parts=[];for(const [k,v] of Object.entries(i)){if(SKIP.has(k))continue;let t=typeof v==='object'&&v!==null?JSON.stringify(v):String(v);if(t.length>80)t=t.slice(0,80)+'…';parts.push(k+'='+t)}return parts.join(' ')}
function row(i){const p=plain(i);if(!p&&!tech.checked)return null;const d=el('details','','ev '+(p?p.tone:'')),s=document.createElement('summary');if(p)s.append(el('span',p.tag,'tag'),p.text);else s.append(el('b',i.event),' '+summary(i));d.append(s,el('pre',JSON.stringify(i,null,2)));return d}
function runSummary(items){let k=items.map(i=>i.event).lastIndexOf('run_started');const m=items.slice(k<0?0:k).filter(i=>i.event==='model_call'),box=document.getElementById('runsum');if(!m.length){box.textContent='Last run: waiting';return}const u=x=>x.usage||{},t=m.reduce((a,x)=>a+(u(x).input_tokens||0)+(u(x).output_tokens||0),0),c=m.filter(x=>typeof x.estimated_usd==='number'),s=m.reduce((a,x)=>a+(x.latency_ms||0),0)/1000;box.textContent='Last run: '+m.length+' model calls · '+t+' tokens · '+(c.length?'about $'+c.reduce((a,x)=>a+x.estimated_usd,0).toFixed(3)+' (estimated)':'cost n/a')+' · model time '+s.toFixed(1)+' s'}
async function refresh(){const r=await fetch('/trace');const items=await r.json();runSummary(items);trace.replaceChildren(...items.map(row).filter(Boolean));trace.scrollTop=trace.scrollHeight}
tech.onchange=refresh;
const WORDS={book:['Approve this booking?','Yes, book it'],cancel:['Approve cancelling this booking?','Yes, cancel it'],reschedule:['Approve rescheduling this booking?','Yes, reschedule']};
function renderPending(p){document.getElementById('simbox').hidden=!p.has_confirmed;if(!p.action_id){pending=null;actions.hidden=true;return}pending=p.action_id;const q=p.proposal||{},w=WORDS[q.action]||WORDS.book,wait=p.status==='pending';document.getElementById('proposal').textContent=(wait?'Waiting for the provider. Check the booking status.':w[0])+' Provider: '+q.provider_id+' · Time: '+q.slot_label+' · Price: ₹'+q.price;document.getElementById('approve').textContent=wait?'Check booking status':w[1];document.getElementById('deny').hidden=wait;actions.hidden=false}
async function syncPending(){renderPending(await (await fetch('/pending')).json())}
function show(result){clearChoices();bubble(result.message||result.error||result.status,'agent');pending=result.status==='approval_required'?result.action_id:null;actions.hidden=!pending;if(result.options&&result.options.length)optionCards(result.options);else if(result.missing&&result.missing.length)missingChips(result.missing);syncPending();refresh()}
document.getElementById('form').onsubmit=async e=>{e.preventDefault();const input=document.getElementById('message'),value=input.value.trim();if(!value)return;input.value='';send(value)};
for(const [id,approved] of [['approve',true],['deny',false]])document.getElementById(id).onclick=()=>{if(pending)run('/approve',{action_id:pending,approved},'Working on it…')};
document.getElementById('simcancel').onclick=()=>run('/simulate-provider-cancel',{},'Working on it…');
refresh();fetch('/pending').then(r=>r.json()).then(p=>{renderPending(p);if(p.action_id)bubble(p.message,'agent');else startChips()});poll(3000);
</script></html>"""


def load_local_key(path: Path):
    if os.getenv("ANTHROPIC_API_KEY") or not path.exists():
        return
    for line in path.read_text().splitlines():
        if line.startswith("ANTHROPIC_API_KEY="):
            os.environ["ANTHROPIC_API_KEY"] = line.split("=", 1)[1].strip().strip('"\'')
            return


def create_server(root: Path, *, scripted=False, scenario="timeout_success", port=8765, model=None):
    root.mkdir(parents=True, exist_ok=True)
    if not scripted and model is None:
        load_local_key(Path(__file__).resolve().parent.parent / ".env")
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("Set ANTHROPIC_API_KEY in the environment or a local .env file")
    db = root / "business.sqlite"
    trace = root / "trace.jsonl"
    provider = MockBookingProvider(db, scenario=scenario, user_id="demo")
    dispatcher = ActionDispatcher(db, provider, trace)
    memory = MemoryStore(db)
    model = model or (ScriptedModel([call("update_request", **FACTS), call("search_providers"),
                            call("hold_slot", provider_id="plumber-1", slot=SLOT), book_held])
             if scripted else AnthropicModel())
    agent = ChatAgent(db, provider, dispatcher, memory, model)
    pending = {"action_id": None}
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id, proposal FROM actions WHERE status IN ('proposed', 'pending') ORDER BY rowid DESC").fetchall()
    pending["action_id"] = next((action_id for action_id, proposal in rows if json.loads(proposal).get("user_id") == "demo"), None)

    busy = threading.Lock()

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
                out = {"has_confirmed": any(b["status"] == "confirmed" for b in provider.list_bookings("demo"))}
                action_id = pending["action_id"]
                if not action_id:
                    return self.send(200, out)
                proposal = dispatcher.presented_proposal(action_id)
                status = dispatcher.status(action_id)
                return self.send(200, {**out, "action_id": action_id, "status": status,
                                       "message": f"Pending {proposal.get('action', 'booking')}: {proposal['provider_id']} at {proposal['slot']} for ₹{proposal['price']}",
                                       "proposal": {"action": proposal.get("action", "book"), "provider_id": proposal["provider_id"],
                                                    "slot": proposal["slot"], "slot_label": _slot_label(proposal["slot"]),
                                                    "price": proposal["price"]}})
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
            if self.path not in {"/chat", "/select", "/approve", "/simulate-provider-cancel"}:
                return self.send(404, {"error": "not found"})
            if not busy.acquire(blocking=False):
                return self.send(409, {"error": "Still working on your last request. Please wait."})
            try:
                data = json.loads(self.rfile.read(size))
                if self.path == "/chat":
                    if pending["action_id"] and dispatcher.status(pending["action_id"]) in {"proposed", "pending"}:
                        result = {"status": "approval_required", "action_id": pending["action_id"],
                                  "message": "Resolve the pending action before starting a new request."}
                    else:
                        result = agent.handle("demo", str(data["text"]))
                        pending["action_id"] = result.get("action_id")
                elif self.path == "/select":
                    if type(data.get("provider_id")) is not str or type(data.get("slot")) is not str:
                        return self.send(400, {"error": "provider_id and slot must be strings"})
                    if pending["action_id"] and dispatcher.status(pending["action_id"]) in {"proposed", "pending"}:
                        return self.send(409, {"error": "resolve the pending action first"})
                    result = agent.select("demo", data["provider_id"], data["slot"])
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
            finally:
                busy.release()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def build_model(args):
    """Model for --replay/--record, or None for the default behaviour."""
    if (args.scripted, bool(args.replay), bool(args.record)).count(True) > 1:
        raise SystemExit("--scripted, --replay and --record are mutually exclusive")
    if args.replay:
        return ReplayModel(args.replay)
    if args.record:
        load_local_key(Path(__file__).resolve().parent.parent / ".env")
        return RecordingModel(AnthropicModel(), args.record)
    return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--scripted", action="store_true", help="offline interface check")
    parser.add_argument("--replay", metavar="PATH", help="replay a recorded cassette offline")
    parser.add_argument("--record", metavar="PATH", help="call the live model and record a cassette")
    parser.add_argument("--scenario", default="timeout_success")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    root = Path.cwd() / "runtime"
    server = create_server(root, scripted=args.scripted, model=build_model(args), scenario=args.scenario, port=args.port)
    print(f"Open http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()
