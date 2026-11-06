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
:root{--bg:#f6f6f4;--surface:#fff;--ink:#17191c;--muted:#5f6670;--line:#e1e1dc;--accent:#b4410a;--accent-strong:#8f3306;--accent-tint:#fbf4ef;--accent-fill:#f0e3d9;--ok:#1c6b3d;--ok-tint:#eef6f0;--warn:#8a4b00;--warn-tint:#fbf3df;--bad:#9b1c1c;--bad-tint:#fbeeee;--info-tint:#eceef1;--you:#eceef1}
*{box-sizing:border-box}
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0;background:var(--bg);color:var(--ink);font-variant-numeric:tabular-nums}
.wrap{max-width:1200px;margin:0 auto;padding:0 20px}
header{background:var(--surface);border-bottom:1px solid var(--line);padding:12px 0}
.top{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}
h1{font-size:17px;font-weight:650;line-height:1.3;margin:0}
.tagline{margin:0;font-size:13px;color:var(--muted)}
.demo{margin-left:6px;padding-left:8px;border-left:1px solid var(--line)}
.how{background:var(--surface);border-bottom:1px solid var(--line);padding:6px 0;font-size:12.5px;color:var(--muted)}
main{display:grid;grid-template-columns:minmax(0,3fr) minmax(0,2fr);gap:16px;padding:16px 20px;max-width:1200px;margin:0 auto;height:calc(100vh - 118px);min-height:640px}
section{display:flex;flex-direction:column;background:var(--surface);border:1px solid var(--line);border-radius:8px;overflow:hidden;min-height:0}
h2{font-size:13px;font-weight:650;letter-spacing:.02em;margin:0;padding:11px 16px;border-bottom:1px solid var(--line);color:var(--ink)}
.body{padding:16px;overflow:auto;flex:1;min-height:140px;white-space:pre-wrap}
[hidden]{display:none!important}
.bubble{padding:0;margin:0 0 14px;max-width:92%;overflow-wrap:anywhere}
.you{background:var(--you);margin-left:auto;padding:8px 12px;border-radius:8px;width:fit-content}
.agent{color:var(--ink)}
form,.actions{display:flex;gap:8px;padding:12px 16px;border-top:1px solid var(--line)}
.actions{flex-wrap:wrap}
#actions.approval-card{flex-direction:column;border:1px solid var(--line);border-top:3px solid var(--accent);margin:12px 16px;border-radius:8px;background:var(--surface);max-height:45%;overflow:auto;flex:none}
.approval-card h3,.confirm-card h3{margin:0 0 8px;font-size:15px;font-weight:650}
.rrow{display:flex;gap:8px;align-items:baseline}
.rrow .lbl{width:70px;color:var(--muted);font-size:13px;flex:none}
.rrow .val{font-weight:600}
.note{margin:8px 0 0;color:var(--muted);font-size:13px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.confirm-card{white-space:normal;border:1px solid var(--line);border-left:3px solid var(--ok);border-radius:8px;background:var(--ok-tint);padding:12px 14px;margin:0 0 14px;max-width:92%}
.confirm-card h3{color:var(--ok)}
.approval-card .hl,.confirm-card .hl{background:var(--warn-tint);color:var(--ink);padding:6px 8px;border-radius:4px;margin:8px 0 0;font-size:13px}
#bookings{display:flex;gap:8px;flex-wrap:wrap;padding:8px 16px;border-top:1px solid var(--line);max-height:96px;overflow:auto}
#bookings .chipb{border:1px solid var(--line);background:var(--bg);border-radius:6px;padding:2px 10px;font-size:13px}
#bookings .chipb.dashed{border-style:dashed}
.st-confirmed{color:var(--ok);font-weight:650}.st-cancelled{color:var(--bad);font-weight:650}.st-rescheduled{color:#175cd3;font-weight:650}
.empty{color:var(--muted);font-size:13px}
input[type=text]{flex:1;min-height:40px;padding:8px 12px;font:inherit;border:1px solid #b9bec5;border-radius:6px;background:#fff;color:var(--ink)}
button{min-height:38px;padding:7px 16px;font:inherit;font-weight:600;background:var(--accent);color:#fff;border:1px solid var(--accent);border-radius:6px;cursor:pointer;transition:background-color .12s,border-color .12s}
button:hover:not(:disabled){background:var(--accent-strong);border-color:var(--accent-strong)}
button.secondary{background:#fff;color:var(--ink);border-color:#b9bec5;font-weight:500}
button.secondary:hover:not(:disabled){background:var(--bg);border-color:var(--ink)}
button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid #1d4ed8;outline-offset:2px}
button:disabled{opacity:.5;cursor:default}
.choices{white-space:normal;margin:0 0 14px;display:flex;flex-wrap:wrap;gap:8px}
.choices>button.secondary{margin:0;text-align:left;min-height:34px;padding:5px 12px;font-size:14px}
.choices .card{flex:1 1 100%;background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.choices .card b{font-size:15px;font-weight:650}
.choices .card div{font-size:13px;color:var(--muted);margin:0 0 6px}
.choices .card button{margin:4px 6px 0 0;min-height:34px;padding:4px 12px;background:#fff;color:var(--ink);border:1px solid #b9bec5;font-weight:500;font-size:14px}
.choices .card button:hover:not(:disabled){background:var(--accent);border-color:var(--accent);color:#fff}
pre{font-size:12px;white-space:pre-wrap;word-break:break-word;margin:4px 0 0;padding:8px;background:var(--bg);border-radius:4px}
.ev{white-space:normal;margin:0 0 8px;font-size:13.5px;word-break:break-word}
.ev summary{cursor:pointer}
.tag{display:inline-block;font-weight:650;font-size:10.5px;line-height:1.6;letter-spacing:.06em;text-transform:uppercase;margin-right:8px;padding:0 6px;border-radius:4px;background:var(--info-tint);color:#3d4652}
.ev.red .tag{background:var(--bad-tint);color:var(--bad)}.ev.green .tag{background:var(--ok-tint);color:var(--ok)}.ev.amber .tag{background:var(--warn-tint);color:var(--warn)}
.legend{font-size:12px;color:var(--muted);padding:7px 16px;border-bottom:1px solid var(--line)}
.cap{font-size:12px;color:var(--muted);margin:4px 0 0;max-width:420px}
#simbox{text-align:right}
#simbox button{min-height:34px;font-size:13px}
#intakebox{border-bottom:1px solid var(--line);padding:0 16px}
#intakebox summary{cursor:pointer;font-weight:650;font-size:13px;padding:10px 0}
#intake{display:grid;grid-template-columns:1fr 1fr;gap:10px 12px;padding:0 0 14px;border:0}
#intake label{display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--muted);font-weight:500}
#intake input,#intake select{min-height:36px;padding:6px 10px;font:inherit;font-size:14px;color:var(--ink);border:1px solid #b9bec5;border-radius:6px;background:#fff}
#intake .wide{grid-column:1/-1}
#intake .presets{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
#intake .presets button{min-height:28px;padding:2px 10px;font-size:12.5px}
#breakbox{font-size:13px;text-align:right;margin-bottom:6px}
.right{display:flex;flex-direction:column;align-items:flex-end}
#breakbox summary{cursor:pointer;color:var(--muted)}
#breakbox .bar button{min-height:32px;font-size:12.5px;padding:4px 10px}
#breakstate{font-size:12px;color:var(--warn);margin-top:4px}
@media(max-width:800px){main{grid-template-columns:1fr;height:auto}section{min-height:420px}#simbox{text-align:left}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
</style><header><div class="wrap top"><div><h1>Orange Errand Agent</h1><p class="tagline">Book home services. You approve every step.<span class="demo">Local demo, mock providers</span></p></div><div class="right"><details id="breakbox"><summary>Try to break it</summary><div class="bar"><button class="secondary" type="button" data-break="timeout">Next booking: provider times out after booking</button><button class="secondary" type="button" data-break="price">Next hold: price rises by ₹50</button><button class="secondary" type="button" data-break="reviews">Ask about reviews (one holds an injected instruction)</button><button class="secondary" type="button" data-break="normal">Back to normal</button></div><div id="breakstate"></div></details><div id="simbox" hidden><button id="simcancel" class="secondary" type="button">Pretend the provider cancels my booking</button><div class="cap">Demo only: pretends your provider cancelled, so you can watch the assistant react without being asked.</div></div></div></div></header><div class="how"><div class="wrap">How this works: 1 Tell me what you need · 2 Pick a provider and time · 3 You approve · 4 Done. I never book without your yes.</div></div><main><section><h2>Chat</h2><details id="intakebox"><summary>What do you need? Start with a short form</summary><form id="intake"><label>Service<input id="f-service" list="dl-service" required placeholder="Plumber"></label><label>Area<input id="f-area" list="dl-area" required placeholder="Indiranagar"></label><label>Day<select id="f-day" required><option value="" selected disabled>Choose a day</option><option value="0">Today</option><option value="1">Tomorrow</option><option value="pick">Pick a date</option></select></label><label>Time of day<select id="f-window"><option value="">Any time</option><option value="mornings only">Mornings only</option><option value="afternoons only">Afternoons only</option><option value="evenings only">Evenings only</option></select></label><label id="f-date-wrap" hidden>Date<input id="f-date" type="date"></label><label class="wide">Budget (₹)<input id="f-budget" type="number" min="1" max="100000" step="1" required placeholder="1500"></label><div class="presets wide" aria-label="Budget presets"><button type="button" class="secondary" data-budget="1000">₹1,000</button><button type="button" class="secondary" data-budget="1500">₹1,500</button><button type="button" class="secondary" data-budget="2000">₹2,000</button><button type="button" class="secondary" data-budget="3000">₹3,000</button></div><label class="wide">Details (optional)<input id="f-notes" type="text" placeholder="Kitchen tap is leaking"></label><div class="wide"><button id="f-go">Find options</button></div><datalist id="dl-service"><option value="Plumber"><option value="Electrician"><option value="AC servicing"></datalist><datalist id="dl-area"><option value="Indiranagar"><option value="Koramangala"><option value="JP Nagar"></datalist></form></details><div id="chat" class="body" aria-live="polite"></div><div id="bookings" hidden aria-label="Your bookings"></div><div id="actions" class="actions approval-card" hidden><div id="proposal"></div><div class="bar"><button id="approve">Yes, book it</button><button id="deny" class="secondary">No, don't</button></div></div><form id="form"><input type="text" id="message" aria-label="Your message" placeholder="Find a plumber this Friday morning under ₹1,500" required><button>Send</button></form></section><section><h2>What the assistant is doing</h2><div class="legend">SAFETY = a guardrail acted · APPROVAL = needs your yes · VERIFY = checked · UNCERTAIN = not known yet</div><div class="legend"><label><input type="checkbox" id="tech"> Show technical detail</label></div><div id="runsum" class="legend">Last run: waiting</div><div id="trace" class="body" aria-live="polite"></div></section></main>
<script>
let pending=null;const chat=document.getElementById('chat'),trace=document.getElementById('trace'),actions=document.getElementById('actions'),tech=document.getElementById('tech');
const DAYS=['Sun','Mon','Tue','Wed','Thu','Fri','Sat'],MONTHS=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function readableTimes(s){return String(s).replace(/(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?/g,(m,y,mo,d,h,mi)=>{const hh=+h;return DAYS[new Date(Date.UTC(+y,+mo-1,+d)).getUTCDay()]+' '+(+d)+' '+MONTHS[+mo-1]+', '+(hh%12||12)+':'+mi+' '+(hh<12?'AM':'PM')})}
const friendly=id=>String(id).replace(/[-_]+/g,' ').replace(/\b\w/g,c=>c.toUpperCase());
let log=[];function store(){try{sessionStorage.setItem('chatlog',JSON.stringify(log.slice(-60)))}catch(e){}}
function bubble(text,kind,temp){const d=document.createElement('div');d.className='bubble '+kind;d.textContent=kind==='agent'?readableTimes(text):text;chat.append(d);chat.scrollTop=chat.scrollHeight;if(!temp){log.push([kind,String(text)]);store()}return d}
function restoreChat(){try{const a=JSON.parse(sessionStorage.getItem('chatlog')||'[]');if(Array.isArray(a))for(const [k,t] of a)if((k==='you'||k==='agent')&&typeof t==='string')bubble(t,k)}catch(e){}}
let timer=null;function poll(ms){clearInterval(timer);timer=setInterval(refresh,ms)}
function setBusy(on){for(const n of document.querySelectorAll('button,#message'))n.disabled=on}
let wait=null,waitLabel='',marker=null;
async function run(path,data,label){setBusy(true);poll(1000);waitLabel=label;marker=lastItems.length?JSON.stringify(lastItems[lastItems.length-1]):null;wait=bubble(label,'agent',true);const w=wait;let out;try{const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});out=await r.json()}catch(e){out={message:'Could not reach the assistant. Please try again.',status:'error'}}finally{w.remove();wait=null;setBusy(false);poll(3000)}show(out)}
function el(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n}
function clearChoices(){for(const n of chat.querySelectorAll('.choices'))n.remove()}
function chipBox(){clearChoices();const d=el('div','','choices');chat.append(d);return d}
function chip(box,label,onclick){const b=el('button',label,'secondary');b.type='button';b.onclick=()=>{clearChoices();onclick()};box.append(b)}
function send(text){bubble(text,'you');return run('/chat',{text},'Thinking…')}
const MISSING={area:['Area',[['Indiranagar','Indiranagar'],['Koramangala','Koramangala'],['JP Nagar','JP Nagar']]],date_range:['Date',[['Today','today'],['Tomorrow','tomorrow']]],budget:['Budget',[['₹1,000','₹1,000'],['₹1,500','₹1,500'],['₹2,000','₹2,000']]],service:['Service',[['Plumber','plumber'],['Electrician','electrician'],['AC servicing','AC servicing']]]};
function missingChips(missing){const box=chipBox();for(const key of missing){const m=MISSING[key==='date'?'date_range':key];if(!m)continue;for(const [label,value] of m[1])chip(box,label,()=>send(m[0]+': '+value))}}
function optionCards(options){const box=chipBox();for(const o of options){const card=el('div','','card');card.append(el('b',o.name),el('div','Rating '+o.rating+' · ₹'+o.price));for(const s of o.slots){const b=el('button',s.label);b.type='button';b.onclick=()=>{clearChoices();run('/select',{provider_id:o.provider_id,slot:s.value},'Thinking…')};card.append(b)}box.append(card)}}
const RED=new Set(['run_error','model_error','web_error','not_approved','status_error']),SKIP=new Set(['event','action_id']);
const T=(tag,text,tone)=>({tag,text,tone:tone||''});
function plain(i){const a=i.arguments||{},r=i.result;switch(i.event){
case 'tool_call':if(i.tool==='update_request'){const p=[a.service,a.area,a.date_range,a.budget?'up to ₹'+a.budget:null].filter(Boolean);return T('AI','Understood your request: '+p.join(' · '))}if(i.tool==='get_provider')return T('PROVIDER','Read reviews of '+friendly(a.provider_id));return null;
case 'tool_result':if(i.tool==='search_providers')return T('PROVIDER','Searched and found '+(Array.isArray(r)?r.length:0)+' providers');if(i.tool==='hold_slot')return T('PROVIDER','Held the slot for 10 minutes at ₹'+(r&&r.price));return null;
case 'review_flagged':return T('SAFETY',"Flagged a risky review and ignored it: '"+i.excerpt+"'",'amber');
case 'price_changed':return T('PRICE','Price changed at hold time: was ₹'+i.was+', now ₹'+i.now+'. Your approval covers the new price.','amber');
case 'select_clicked':return T('YOU','You picked '+friendly(i.provider_id)+' at '+readableTimes(i.slot));
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
function runSummary(items){const box=document.getElementById('runsum'),k=items.map(i=>i.event).lastIndexOf('run_started');if(k<0){box.textContent='Last run: waiting';return}const run=items.slice(k),m=run.filter(i=>i.event==='model_call');if(!m.length){box.textContent=run.some(i=>i.event==='run_outcome')?'Last run: answered by code, no model call':'Last run: waiting';return}const u=x=>x.usage||{},t=m.reduce((a,x)=>a+(u(x).input_tokens||0)+(u(x).output_tokens||0),0),c=m.filter(x=>typeof x.estimated_usd==='number'),s=m.reduce((a,x)=>a+(x.latency_ms||0),0)/1000;box.textContent='Last run: '+m.length+' model calls · '+t+' tokens · '+(c.length?'about $'+c.reduce((a,x)=>a+x.estimated_usd,0).toFixed(3)+' (estimated)':'cost n/a')+' · model time '+s.toFixed(1)+' s'}
let lastItems=[],lastBookings=[];
function liveLine(items){let i=marker?items.map(x=>JSON.stringify(x)).lastIndexOf(marker):-1;const fresh=items.slice(i+1);for(let k=fresh.length-1;k>=0;k--){const p=plain(fresh[k]);if(p)return p.text}return ''}
async function refresh(){const items=await (await fetch('/trace')).json();lastItems=items;runSummary(items);const rows=items.map(row).filter(Boolean);trace.replaceChildren(...(items.length?rows:[el('div','Watch the assistant work here once you send a request.','empty')]));trace.scrollTop=trace.scrollHeight;if(wait){const t=liveLine(items);wait.textContent=waitLabel+(t?' '+readableTimes(t):'')}refreshBookings()}
async function refreshBookings(){try{lastBookings=(await (await fetch('/bookings')).json()).bookings||[]}catch(e){}renderBookings()}
function renderBookings(){const box=document.getElementById('bookings');box.replaceChildren();box.hidden=!lastBookings.length;for(const b of lastBookings){const c=el('span','','chipb');c.append(friendly(b.provider)+' · '+b.slot_label+' · ',el('span',b.status[0].toUpperCase()+b.status.slice(1),'st-'+b.status));box.append(c)}if(lastBookings.length&&pendingKind==='replacement')box.append(el('span','Replacement waiting for your yes','chipb dashed'))}
tech.onchange=refresh;
const WORDS={book:['Approve this booking?','Yes, book it'],cancel:['Approve cancelling this booking?','Yes, cancel it'],reschedule:['Approve rescheduling this booking?','Yes, reschedule']};
let pendingKind=null,curProposal=null;
function renderPending(p){document.getElementById('simbox').hidden=!p.has_confirmed;pendingKind=p.kind||null;if(!p.action_id){pending=null;actions.hidden=true;renderBookings();return}pending=p.action_id;const q=p.proposal||{},w=WORDS[q.action]||WORDS.book,wait=p.status==='pending';curProposal=q;
const box=document.getElementById('proposal'),rows=[['Provider',friendly(q.provider_id)],['When',q.slot_label],['Price','₹'+q.price]];
box.replaceChildren(el('h3',wait?'Waiting for the provider. Check the booking status.':p.kind==='replacement'?'Your booking was cancelled. Approve a replacement?':w[0]));
if(p.replaces)box.append(el('div','Replaces your cancelled booking: '+friendly(p.replaces.provider)+', '+p.replaces.slot_label,'note'));
for(const [l,v] of rows){const r=el('div','','rrow');r.append(el('span',l,'lbl'),el('span',v,'val'));box.append(r)}
{const pc=[...lastItems].reverse().find(i=>i.event==='price_changed'&&i.provider_id===q.provider_id&&i.slot===q.slot&&i.now===q.price);if(pc&&!wait)box.append(el('div','Price changed from ₹'+pc.was+' to ₹'+pc.now+' since the options list. Check it before you approve.','hl'))}
if(!wait)box.append(el('p','Nothing is booked until you click Yes.','note'));
document.getElementById('approve').textContent=wait?'Check booking status':p.kind==='replacement'&&q.action==='book'?'Yes, book the replacement':w[1];document.getElementById('deny').hidden=wait;actions.hidden=false;renderBookings()}
async function syncPending(){renderPending(await (await fetch('/pending')).json())}
const SENT={slot_taken:'That slot was just taken. Nothing was booked.',not_booked:'The booking did not go through. Nothing was booked.',slot_conflict:'That time clashes with another booking. Nothing was booked.',not_approved:'Okay, nothing was done.',cancelled:'Your booking was cancelled.',rescheduled:'Your booking was rescheduled.',hold_expired:'That hold expired. Nothing was booked.'};
function confirmCard(q,items){const c=el('div','','confirm-card');c.append(el('h3','Booking confirmed'));for(const [l,v] of [['Provider',friendly(q.provider_id)],['When',q.slot_label],['Price','₹'+q.price]]){const r=el('div','','rrow');r.append(el('span',l,'lbl'),el('span',v,'val'));c.append(r)}c.append(el('p','Booked. One booking, verified.','note'));
let k=items.map(i=>i.event).lastIndexOf('approved');const after=items.slice(k<0?0:k).map(i=>i.event),t=after.indexOf('booking_timeout');if(t>=0&&after.slice(t).some(e=>e==='status_checked'||e==='confirmed'))c.append(el('div',"The provider didn't confirm in time, so I asked it directly. It confirmed once; nothing was booked twice.",'hl'));chat.append(c);chat.scrollTop=chat.scrollHeight}
async function show(result){clearChoices();const msg=result.message||result.error||result.status,st=result.status;pending=st==='approval_required'?result.action_id:null;if(!pending)actions.hidden=true;
if(st==='confirmed'&&curProposal&&curProposal.action==='book'){let items=lastItems;try{items=await (await fetch('/trace')).json()}catch(e){}confirmCard(curProposal,items);log.push(['agent','Booking confirmed: '+friendly(curProposal.provider_id)+', '+curProposal.slot_label+', ₹'+curProposal.price]);store()}
else if(result.options&&result.options.length&&/^Options:/.test(msg)){const lbl=String(result.options[0].slots&&result.options[0].slots[0]?result.options[0].slots[0].label:'');bubble('Here are '+result.options.length+' options'+(lbl?' for '+lbl.split(',')[0]:'')+'. Pick a time.','agent')}
else if(!(st==='approval_required'&&result.action_id&&/^(Approve |Pending |Your .* booking was cancelled\. Approve)/.test(msg)))bubble(SENT[st]||msg,'agent');
if(result.options&&result.options.length)optionCards(result.options);else if(result.missing&&result.missing.length)missingChips(result.missing);refresh().then(syncPending)}
const intakeBox=document.getElementById('intakebox'),pad=n=>String(n).padStart(2,'0');
function isoDay(offset){const d=new Date();d.setDate(d.getDate()+offset);return d.getFullYear()+'-'+pad(d.getMonth()+1)+'-'+pad(d.getDate())}
document.getElementById('f-day').onchange=e=>{document.getElementById('f-date-wrap').hidden=e.target.value!=='pick'};
for(const b of document.querySelectorAll('[data-budget]'))b.onclick=()=>{document.getElementById('f-budget').value=b.dataset.budget};
document.getElementById('intake').onsubmit=e=>{e.preventDefault();const v=id=>document.getElementById(id).value.trim(),day=v('f-day'),date=day==='pick'?v('f-date'):isoDay(+day);if(!date){document.getElementById('f-date').focus();return}
const text=v('f-service')+' in '+v('f-area')+' on '+date+(v('f-window')?', '+v('f-window'):'')+', under ₹'+v('f-budget')+'.'+(v('f-notes')?' Details: '+v('f-notes')+'.':'');intakeBox.open=false;send(text)};
const BREAK_NOTE={timeout:'Next booking: the provider will book it but the reply will be lost.',price:'Next hold: the quoted price will be ₹50 higher than the search price.',normal:''};
for(const b of document.querySelectorAll('[data-break]'))b.onclick=async()=>{const kind=b.dataset.break;if(kind==='reviews'){send('Before I choose, read the reviews of the providers you found and tell me if anything looks off.');return}
let out;try{out=await (await fetch('/break',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind})})).json()}catch(e){out={}}
document.getElementById('breakstate').textContent=out.status==='ok'?BREAK_NOTE[kind]:'Could not change the mode.'};
document.getElementById('form').onsubmit=async e=>{e.preventDefault();const input=document.getElementById('message'),value=input.value.trim();if(!value)return;input.value='';send(value)};
for(const [id,approved] of [['approve',true],['deny',false]])document.getElementById(id).onclick=()=>{if(pending)run('/approve',{action_id:pending,approved},'Working on it…')};
document.getElementById('simcancel').onclick=()=>run('/simulate-provider-cancel',{},'Working on it…');
restoreChat();refresh();fetch('/pending').then(r=>r.json()).then(p=>{renderPending(p);if(!p.action_id&&!chat.children.length)intakeBox.open=true});poll(3000);
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

    def friendly(provider_id):
        return str(provider_id).replace("-", " ").replace("_", " ").title()

    def booking_slot(booking):
        return {"provider": friendly(booking["provider"]), "slot_label": _slot_label(booking["slot"])}

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

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
                with sqlite3.connect(db) as conn:
                    replaced = conn.execute("SELECT booking_id FROM replacement_events WHERE action_id = ?", (action_id,)).fetchone()
                kind = "replacement" if replaced else {"book": "booking"}.get(proposal.get("action", "book"), proposal.get("action"))
                extra = {}
                if replaced:
                    original = next((b for b in provider.list_bookings("demo") if b["id"] == replaced[0]), None)
                    if original:
                        extra["replaces"] = booking_slot(original)
                return self.send(200, {**out, **extra, "kind": kind, "action_id": action_id, "status": status,
                                       "message": f"Pending {proposal.get('action', 'booking')}: {proposal['provider_id']} at {proposal['slot']} for ₹{proposal['price']}",
                                       "proposal": {"action": proposal.get("action", "book"), "provider_id": proposal["provider_id"],
                                                    "slot": proposal["slot"], "slot_label": _slot_label(proposal["slot"]),
                                                    "price": proposal["price"]}})
            if self.path == "/bookings":
                items = []
                for b in provider.list_bookings("demo"):
                    try:
                        price = provider.booking_price(b["id"])
                    except Exception:
                        price = None
                    items.append({"id": b["id"], **booking_slot(b), "price": price, "status": b["status"]})
                return self.send(200, {"bookings": items})
            self.send(404, {"error": "not found"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                return self.send(403, {"error": "local origin required"})
            if self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                return self.send(415, {"error": "JSON required"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self.send(400, {"error": "invalid Content-Length"})
            if size < 1 or size > 24000:
                return self.send(413, {"error": "request too large"})
            if self.path not in {"/chat", "/select", "/approve", "/simulate-provider-cancel", "/break"}:
                return self.send(404, {"error": "not found"})
            try:
                raw = self.rfile.read(size)  # read before taking the lock: a stalled client must not block others
            except OSError:
                return self.send(408, {"error": "request body timed out"})
            if not busy.acquire(blocking=False):
                return self.send(409, {"error": "Still working on your last request. Please wait."})
            try:
                data = json.loads(raw)
                if self.path == "/chat":
                    if pending["action_id"] and dispatcher.status(pending["action_id"]) in {"proposed", "pending"}:
                        result = {"status": "approval_required", "action_id": pending["action_id"],
                                  "message": "Resolve the pending action before starting a new request."}
                    else:
                        if type(data["text"]) is not str:
                            return self.send(400, {"error": "text must be a string"})
                        result = agent.handle("demo", data["text"])
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
                elif self.path == "/break":
                    # Demo switches for the provider mock only; approval and verification code are untouched.
                    kind = data.get("kind")
                    if kind == "timeout":
                        provider.scenario = "timeout_success"
                    elif kind == "price":
                        provider.quote_delta = 50
                    elif kind == "normal":
                        provider.scenario, provider.quote_delta = "success", 0
                    else:
                        return self.send(400, {"error": "unknown kind"})
                    result = {"status": "ok", "kind": kind}
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
