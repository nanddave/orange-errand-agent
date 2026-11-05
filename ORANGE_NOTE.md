# From errands to money: how this gate fits Orange

**The idea in one line.** The assignment is an agent that may only act after the user says an exact yes, and that proves what happened before it reports success. Orange's manifesto describes a WhatsApp assistant (Padii) that guides people through money decisions, starting with investing. A money action is an errand where a mistake costs more, so the same rules apply, only stricter.

## How each mechanism carries over

| Here (home services) | At Orange (investing on WhatsApp) |
|---|---|
| The user approves the exact provider, slot and price; any change voids it | "Invest ₹5,000 in Fund X? Reply YES." A different amount or fund needs a new yes. |
| The idempotency key is stored before the provider call; after a timeout, ask the provider before retrying | An order request times out: check the order status under the same key before telling the user anything. Never invest twice. |
| Reviews are untrusted data and cannot change the request | Factsheets, news, forwarded messages and finfluencer tips are untrusted. They cannot change the amount, the fund or the approval. |
| Inferred preferences stay unused until the user confirms them | "You sold in the last dip" suggests low risk appetite. Padii shows that guess and uses it only after the user agrees. |
| A provider cancellation triggers a replacement proposal, never a silent rebooking | A market drop or idle cash triggers an unprompted nudge with an explanation and a proposal. No trade without a yes. |
| Bounded turns, tool calls, time, input, and a per-run cost line | Hard caps and a cost line per conversation, which matters at WhatsApp scale. |
| A trace records what was shown, approved and verified | An audit trail for disputes and compliance review. |
| 500 seeded fault-injection runs, 0 violations | The same checker, extended to order states, is how I would argue "safe enough to touch money". |

## What this does not cover

- No real broker, identity checks, suitability rules or regulation. I found no public statement of Orange's regulatory approach, so I make no assumption about it.
- WhatsApp specifics: duplicate webhook deliveries (the same idempotency idea, but they need their own tests), message templates, and delivery receipts.
- Speed. A reply takes 10 to 20 seconds because the model makes several tool calls in turn. A chat product needs fewer.
- Orange's actual architecture is unknown to me; this note is an inference from the public manifesto only.

## What I would do first

1. Swap the mock provider for a broker sandbox and add its real failure modes to the fuzz.
2. Add duplicate WhatsApp webhooks as a fault type.
3. Cut tool turns so a reply lands in a few seconds.

Source: [Orange manifesto](https://www.orangeinvest.app/Manifesto).
