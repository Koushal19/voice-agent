# AI Voice Receptionist — COM1PBX + Sarvam
## Product Requirements Document

**Version:** 1.0
**Date:** 2026-08-24
**Site:** SPC Hospital, Salem (STD code 0427)
**Numbers in scope:** 0427-2529500, 0427-2312020
**Telephony:** COM1PBX (existing — retained, not replaced)
**AI stack:** Sarvam (STT + LLM + TTS)
**Human fallback:** Extensions 122 / 123

> **Relationship to `docs/PRD.md`.** That document describes a LiveKit-based design for a
> Chennai deployment. This PRD deliberately takes the opposite architectural position — no
> LiveKit, no LangGraph, no OpenRouter — and is scoped to the Salem COM1PBX site.
> Sections 20–22 explain why each of those was *rejected*, not merely omitted.

---

# 0. THE ANSWER TO THE MOST IMPORTANT QUESTION

> **"If Caller A is connected to the AI agent through COM1PBX and Caller B calls the same
> hospital number, will Caller B wait because Caller A is still on the number, or will
> COM1PBX forward Caller B to another independent AI session?"**

## 0.1 The short answer

**Neither Scenario A nor Scenario B as written is correct, because both assume the phone
*number* is the thing that gets occupied. In a SIP/IP-PBX system it is not.**

A DID such as 0427-2529500 is a **routing label**, not a physical line. Nothing "occupies
the number." What actually gets occupied, in this order, is:

1. **A trunk channel** on the link between the telco and COM1PBX (a PRI B-channel, or a
   concurrent-call slot on a SIP trunk).
2. **A destination endpoint** — whatever the inbound route points at (an extension, ring
   group, queue, IVR, or another trunk).
3. **A concurrent-call licence slot**, if COM1PBX licenses by concurrency.

So the real answer is:

> **Caller B gets a fully independent AI session — separate SIP dialog, separate RTP media
> stream, separate application session, separate Sarvam connections — provided that (a) a
> free trunk channel exists, and (b) the AI is configured on COM1PBX as a multi-channel
> destination (a SIP trunk/peer, or a queue fronting multiple AI registrations) rather than
> as a single SIP extension.**
>
> **If the AI is configured as one ordinary SIP extension, Caller B will hit busy or
> call-waiting** — not because the number is occupied, but because that extension already
> has an active call and most PBX extension profiles default to `call-limit = 1`.

That last sentence is the entire practical risk, and it is a **configuration choice you
control**, not a limitation of COM1PBX.

## 0.2 The correction that matters most for capacity

Scenario B contains an assumption that is **false on every PBX**:

> *"...and immediately make the original inbound number/channel available to accept Caller B"*

**Forwarding does not release the inbound channel.** When COM1PBX routes Caller A to the
AI, it does not hand the call off and walk away. It **bridges two call legs**:

```
   Telco trunk leg                              AI leg
   (inbound, HELD OPEN)  <---- COM1PBX ---->   (outbound to AI endpoint)
          |                                            |
   consumes 1 trunk channel                    consumes 1 AI channel
   for the ENTIRE call duration                for the ENTIRE call duration
```

Both legs stay up for the whole conversation. The only exception is a SIP REFER / release-
link transfer accepted end-to-end by the telco, which Indian PSTN trunks essentially never
grant for inbound calls.

### The consequence — read this twice

**The hospital's trunk channel count is a hard ceiling on AI concurrency, and no amount of
AI engineering can raise it.**

| If the hospital's trunk is... | Max simultaneous inbound calls (AI *and* human, combined) |
|---|---|
| 2 x 4-line analog / FXO | **8** |
| 8-channel SIP trunk | **8** |
| 16-channel SIP trunk | **16** |
| Single E1 PRI | **30** |
| 2 x E1 PRI | **60** |

If the hospital today has an 8-channel trunk, **25 concurrent AI calls is physically
impossible** regardless of how good the AI server is. Channels must be bought from the
telco. **This is Gate 0 of the project** (§29, Q-1).

## 0.3 Why call waiting happens today — four independent causes

The current symptom ("multiple calls arrive, extensions busy, callers wait") can be
produced by four different mechanisms. They need different fixes, and only two are solved
by adding an AI:

| # | Cause | Symptom | Does the AI fix it? |
|---|---|---|---|
| 1 | **Trunk channels exhausted** | Caller hears telco busy tone / "all lines busy". The call never reaches COM1PBX at all — and never appears in the CDR. | **No.** Only the telco can fix this. |
| 2 | **Destination extension busy** (`call-limit=1` on 122/123) | Call reaches the PBX, then queues, rings busy, or goes to call-waiting | **Yes** — the AI absorbs the call before it ever reaches 122/123. |
| 3 | **Queue configuration** (all agents busy, callers held with music-on-hold) | Caller hears hold music, possibly a position announcement | **Yes**, substantially. |
| 4 | **Concurrent-call licence limit** on COM1PBX | Calls rejected or held at a fixed count regardless of trunk capacity | **No.** Requires a licence upgrade. |

**Determine which of these is actually happening before building anything.** The COM1PBX
CDR export tells you: cause 1 is *invisible* in the CDR (ask the telco); causes 2 and 3
show as long `ringing`/`queue` durations; cause 4 shows as a hard plateau in the
simultaneous-call count.

## 0.4 Point-by-point answers to the ten sub-questions

| # | Question | Answer | Confidence |
|---|---|---|---|
| 1 | What happens to the original inbound channel after forwarding? | **It stays occupied for the entire call.** COM1PBX bridges the inbound leg to the AI leg; the channel is released only on hangup. | **High** — universal SIP/PBX behaviour, not vendor-specific |
| 2 | Does the AI call consume a PBX channel? | **Yes — two.** One inbound trunk channel plus one outbound channel toward the AI. Both count against total concurrency if COM1PBX licenses by concurrent calls. | **High** |
| 3 | Does the AI call consume an extension? | **Only if you configure the AI as an extension.** As a SIP trunk/peer it consumes a *channel on that trunk*, and trunk channel limits are normally configurable and much greater than 1. **This is the single most important configuration decision in the project.** | **High** |
| 4 | Does the number become available for another caller? | **The number was never unavailable.** Numbers have no busy state in SIP. Trunk channels do. | **High** |
| 5 | How does COM1PBX handle concurrent inbound calls? | Standard IP-PBX behaviour: each inbound call is an independent SIP dialog with its own Call-ID, its own RTP session, and its own dialplan execution context. Concurrency is bounded by trunk channels, destination call-limit, and licence. | **High** on the mechanism; **UNVERIFIED** on this product's specific limits |
| 6 | Is there a configurable concurrency / licence / channel limit? | COM1PBX markets itself as licence-activated ("activate multiple applications with simple licences"), which strongly implies **yes, a licensed concurrency ceiling exists.** The value is unknown. | **UNVERIFIED — must ask COM1PBX** |
| 7 | What causes call waiting? | One of the four causes in §0.3. Most commonly #2 (`call-limit=1` on the two receptionist extensions). | **Medium** — confirm from CDR |
| 8 | Does external SIP forwarding behave differently from internal extension forwarding? | **Yes, and importantly.** An internal extension has a busy/registration state and a low call-limit. An external SIP trunk/peer has neither by default — it is stateless to the PBX and accepts as many simultaneous INVITEs as its channel limit allows. **This is precisely why the AI must be a trunk, not an extension.** | **High** on the general rule; **UNVERIFIED** for COM1PBX's trunk implementation |
| 9 | Can the PBX simultaneously forward multiple calls to the AI endpoint? | **Yes, if the AI destination is a trunk with its channel limit set to >= 25.** No, if it is a single extension with call-limit 1. | **High** on the rule; **UNVERIFIED** that COM1PBX exposes the setting |
| 10 | Does each AI call get its own independent SIP/media session? | **Yes.** Each call is a distinct INVITE -> distinct Call-ID -> distinct SDP negotiation -> distinct RTP port pair. There is no shared media path. Isolation at the telephony layer is automatic; the isolation *risk* lives in your application code, not in SIP (§17). | **High** |

## 0.5 What must be verified with COM1PBX

COM1PBX (com1pbx.com — an Indian IP-PBX appliance vendor operating since the 1990s;
contact `srini@trust.co.in`, +91 98840 19019) **publishes no admin guide, no technical
specification, and no API reference publicly.** Their site describes an "all-in-one AI Box"
built on open hardware with licence-activated applications (IP PBX, inbound/outbound call
centre, call blasting, bridge conferencing, IP paging) — but no numbers.

> **This must be verified with COM1PBX.**

Everything marked UNVERIFIED above is unverifiable from open sources. Send the following
verbatim to COM1PBX support and require written answers.

### Questions to send to COM1PBX support

```
Subject: Technical capability confirmation - AI voice agent integration
Site: SPC Hospital, Salem. DIDs 0427-2529500 and 0427-2312020.

 1. What is the maximum number of concurrent inbound calls our system can
    receive on a single DID (0427-2529500)? Is this limited by the number, by
    the trunk, or by licence?

 2. How many concurrent SIP/trunk channels do our current licence and hardware
    support? What model and licence tier is installed at our site?

 3. What is the maximum number of calls that can be simultaneously routed from
    an inbound route to a single external SIP destination?

 4. When a call is forwarded from an inbound route to an external SIP
    destination, is the original inbound trunk channel released, or is it held
    for the duration of the call? (We expect "held" - please confirm.)

 5. Can multiple simultaneous calls be routed to the same external SIP endpoint
    (same IP:port)? If yes, which configuration parameter controls the maximum
    simultaneous calls to that endpoint, and what is its maximum value?

 6. Is call waiting on our system configured at extension level, trunk level, or
    queue level? Please send the current configuration for extensions 122 and
    123 and for the inbound routes on both DIDs.

 7. Is forwarding to an external SIP destination (a SIP peer on our own LAN, not
    a PSTN number) supported? Does it require an additional licence?

 8. What are our current SIP trunk / PRI channel limits, and who is the upstream
    telco for each DID?

 9. Is concurrent-call capacity licensed? If so, what is our current limit, and
    what does it cost to raise it to 30 concurrent calls?

10. Can an inbound route automatically fail over to a fallback extension or ring
    group if the primary SIP destination is unreachable, returns a 5xx response,
    or does not answer within N seconds? What is the failover timeout and is it
    configurable?

11. Does COM1PBX accept SIP REFER for call transfer initiated by an external SIP
    endpoint? If not, what transfer mechanism is supported (re-INVITE, AMI/API,
    DTMF-triggered feature code)?

12. Does COM1PBX expose an API, AMI/ARI interface, or webhooks for call events
    and call control? If yes, please send the documentation.

13. Which codecs are supported on internal SIP trunks - specifically G.711 a-law
    and u-law? Is transcoding to/from G.729 required?

14. Please provide a CDR export for the last 30 days for both DIDs, including
    per-call start time, answer time, end time, disposition, destination, and
    the simultaneous-call count.
```

**Chase question 14 hardest.** The CDR settles §0.3 empirically and sizes the entire
system. Everything else is secondary.

## 0.6 A finding from your own data that changes the sizing

`docs/AUDIO FILES SPC/` contains 117 call recordings from a single day, 2026-08-15:

| Prefix | Meaning | Count |
|---|---|---|
| `OG_` | Outgoing | 67 |
| `IC_` | Incoming (answered) | 34 |
| `AB_` | Abandoned | 16 |

Inbound calls by hour on that day:

| Hour | 10 | 11 | 12 | 13 | 14 | 15 | 16 |
|---|---|---|---|---|---|---|---|
| Inbound (IC+AB) | 1 | 1 | 5 | 10 | **15** | 10 | 8 |
| All calls | 1 | 2 | 9 | 16 | **37** | 36 | 16 |

Peak inbound hour is 15 calls. At an average handle time of 3 minutes that is
**0.75 Erlangs of offered inbound traffic** — a realistic peak concurrency of **2–4
simultaneous inbound calls**, not 25. Including outbound, the busiest hour reaches roughly
1.9 Erlangs, or **5–6 simultaneous channels**.

Two conclusions:

1. **The 25-concurrency target is roughly 5–8x observed peak load.** Designing the software
   ceiling at 25 is correct and cheap — it costs nothing to support. **Buying** for 25 (a
   Sarvam Business plan, 25+ telco channels, multiple servers) is not justified by this
   data. Build for 25, provision for 8, and let metrics drive the rest (§24).
2. **16 abandoned out of ~50 inbound calls is a 32% abandonment rate.** That is the actual
   business problem, and it is the metric this project should be judged on.

**Caveat, stated plainly:** these are *recordings*, not the CDR. Recording may be enabled
selectively, and the sample is one day. **The CDR (question 14) is the authority.** Do not
size the telco contract from this table.

Recording filenames also reveal live routing facts worth confirming: a route named
`AA_New_IN-SLM` (27 of 34 inbound calls) is almost certainly an existing **Auto Attendant
for Salem inbound** — which is the natural, lowest-risk insertion point for the AI (§11).
Other destinations seen: extensions `600`, `111`, `244`, `666`, `616`, `122`, and named
targets `SM_Dr Bala`, `SM_Pharmacy`, `SM_OT THEATRE`, `SM_FEMALE WARD`. The extension map
is larger than 122/123 and must be documented before transfer logic is written.

---

# 1. Executive Summary

SPC Hospital, Salem receives inbound patient calls on two DIDs terminating on a COM1PBX
IP-PBX. Calls reach an auto attendant and then human receptionists on extensions 122/123.
A sample day shows roughly a third of inbound calls abandoned.

This project inserts an **AI voice receptionist** between the PBX and the humans. The AI
answers immediately in Tamil, English, or code-mixed Tanglish; handles doctor lookup,
availability and appointment booking against a deterministic booking API; answers a fixed
FAQ set; and transfers anything else to 122/123. It never diagnoses, never invents
availability, and never leaves a caller at silence.

**Architectural stance:** COM1PBX stays the telephony system. The AI is a **SIP trunk peer
on the hospital LAN** — one more destination the PBX can route to, with an automatic
failover back to 122/123 if it is unreachable. The AI stack is **Sarvam end to end**
(Saaras v3 streaming STT, Sarvam-105B chat, Bulbul TTS). Conversation control is a
**deterministic finite state machine** with LLM tool-calling inside states, not an agent
framework. No LiveKit, no LangGraph, no OpenRouter, no Kubernetes.

**Runtime:** one Linux box on the hospital LAN running Asterisk (as a single-purpose SIP/
media gateway, *not* as a PBX) plus a Python/FastAPI application. Postgres for the system
of record; Redis only for cross-process coordination. Software ceiling 25 concurrent calls;
initial provisioning for 8.

**The blocking unknown is telephony, not AI.** Section 0.5 lists what COM1PBX must confirm
before a single line of integration code is worth writing.

---

# 2. Problem Statement

| Observation | Evidence | Impact |
|---|---|---|
| ~32% of inbound calls are abandoned | 16 `AB_` vs 34 `IC_` recordings on 2026-08-15 | Lost appointments; patients call competitors |
| Only two receptionist extensions absorb general inbound | 122/123 | Two calls in parallel, then queue |
| Peak load is concentrated | 15 inbound calls in the 14:00 hour vs 1 at 10:00 | Staffing for peak is uneconomic; staffing for average guarantees abandonment |
| A large share of calls are routine | Appointment booking, timings, department and doctor questions | Human time spent on work that does not require a human |
| Callers speak Tamil, English, and mixed Tanglish | Salem catchment | A rigid DTMF IVR cannot serve them; the existing auto attendant handles 27 of 34 inbound calls but only routes, it does not resolve |

**The problem is not "we need AI."** The problem is: *routine inbound calls are queueing
behind two humans and a third of callers give up.* The AI is the cheapest available way to
raise effective inbound capacity without hiring, provided it never degrades the experience
for the calls it cannot handle.

---

# 3. Existing System

## 3.1 What is in place today

```
   Patients
      |
      +-- 0427-2529500 --+
      |                  |
      +-- 0427-2312020 --+
                         |
                    [ telco trunk ]        <-- channel count UNKNOWN (Gate 0)
                         |
                    +----------+
                    | COM1PBX  |            IP-PBX appliance, on-prem
                    +----------+
                         |
                 Inbound route -> "AA_New_IN-SLM" (Auto Attendant)
                         |
        +----------------+----------------+-------------------+
        |                |                |                   |
     Ext 122          Ext 123        Ext 111/244/600/616/666  Named targets
   (reception)      (reception)        (departments)      (Dr Bala, Pharmacy,
                                                           OT Theatre, Female Ward)
```

## 3.2 Known facts

- COM1PBX is an on-premises IP-PBX appliance, licence-activated, built on open hardware.
- Call recording is enabled, producing MP3 files named
  `{IC|OG|AB}_{YYYYMMDDHHMMSS}_{route-or-ext}_{caller-number}.mp3`.
- An auto attendant (`AA_New_IN-SLM`) already fronts Salem inbound calls.
- The extension plan is larger than 122/123 and includes named department destinations.

## 3.3 Unknown and blocking

| Unknown | Why it blocks | Source of truth |
|---|---|---|
| Telco trunk type and channel count | Hard ceiling on all concurrency | Telco + COM1PBX Q-8 |
| COM1PBX concurrent-call licence limit | Second hard ceiling | COM1PBX Q-2, Q-9 |
| Whether external SIP peers are supported | Decides the entire integration approach | COM1PBX Q-7 |
| Whether SIP REFER from an external peer is honoured | Decides the transfer mechanism | COM1PBX Q-11 |
| Inbound-route failover behaviour | Decides whether the safety net lives in the PBX or only in the app | COM1PBX Q-10 |
| Real call volume and concurrency distribution | Sizes everything | COM1PBX Q-14 (CDR) |
| Full extension map and department ownership | Needed for transfer logic beyond 122/123 | Hospital operations |

---

# 4. Proposed System

The AI becomes **one additional routing destination on COM1PBX**, sitting between the auto
attendant and the human extensions.

```
                        Patients
                           |
              0427-2529500 / 0427-2312020
                           |
                      [ telco trunk ]
                           |
                       COM1PBX
                           |
                    Inbound route
                           |
                  Auto Attendant (existing)
                           |
              "Press 1 / stay on the line for appointments"
                           |
                           v
        +==================================================+
        |  SIP trunk peer: AI-GATEWAY (LAN, channels=25)   |
        +==================================================+
                           |
                   AI Voice Server (on-prem Linux box)
                           |
        +------------------+------------------+
        |                                     |
   Asterisk (SIP + RTP + AudioSocket)   Python app (FastAPI)
        |                                     |
        +------------ raw 8 kHz PCM ----------+
                                              |
                          +-------------------+-------------------+
                          |                   |                   |
                    Sarvam STT          Sarvam LLM          Sarvam TTS
                  (saaras:v3 WS)      (sarvam-105b)        (bulbul WS)
                                              |
                                     Deterministic FSM
                                              |
                          +-------------------+-------------------+
                          |                                       |
                  Booking API (Postgres)                  Out-of-scope / escalation
                          |                                       |
                    Appointment created                   SIP REFER -> 122 / 123
                                                                  |
                                                          Human receptionist
```

**Failover path, configured in COM1PBX and independent of the AI:**

```
   Inbound route -> AI-GATEWAY trunk
                       |
                       +-- unreachable / 503 / no answer in 4s
                                    |
                                    v
                       Ring group [122, 123]  (existing behaviour)
```

This is the most important reliability control in the system and **it lives in the PBX,
not in the AI**. If the AI box is powered off, the hospital's phones behave exactly as they
do today.

---

# 5. Goals

| # | Goal | Measure | Target |
|---|---|---|---|
| G1 | Answer every inbound call immediately | Time to first AI audio | < 2s from PBX answer, 100% of calls |
| G2 | Resolve routine calls without a human | Calls completed by AI / total AI-handled | >= 50% after 4 weeks (baseline set in week 2) |
| G3 | Cut abandonment | Abandoned calls / inbound calls | From ~32% to < 10% |
| G4 | Never strand a caller | Calls ending in silence, dead air > 8s, or unintended hangup | **0** |
| G5 | Natural conversational latency | End-of-speech to first AI audio byte, p95 | <= 1.5s (hard ceiling 2.0s) |
| G6 | Serve the actual language mix | Tamil / English / Tanglish handled without the caller switching | Qualitative review of 100 calls |
| G7 | Never invent clinical or scheduling facts | Appointments created that do not match a real open slot | **0** |
| G8 | Support 25 concurrent calls in software | Load test at 25 simulated calls | p95 latency holds under G5 |

---

# 6. Non-Goals

| Out of scope | Reason |
|---|---|
| Medical diagnosis, advice, or triage recommendations | Clinical liability. The AI collects a reason-for-visit string; it does not interpret it. |
| Replacing COM1PBX | The PBX is the telephony system of record and the failover path. §26. |
| Outbound calling (reminders, follow-ups, campaigns) | Different product, different consent regime, different DoT considerations. Phase 2. |
| Being the hospital's only phone answering path | The AI is one destination among many. Humans and the existing auto attendant remain. |
| EMR / HIS write-back, ABDM / ABHA integration | No integration specified. Booking lives in this system's own Postgres for MVP. |
| Multi-region or cloud-hosted deployment | On-prem removes latency, cost, and PSTN-to-internet regulatory questions. §23. |
| Kubernetes, microservices, message buses | Not justified at 25 concurrent calls. §29. |
| Payment collection over the phone | Prohibited. Out of scope indefinitely. |
| WhatsApp / SMS confirmations | Phase 2 (§27). MVP confirms verbally and logs. |
| Horizontal autoscaling | One box handles 25 calls with 3x headroom (§24). |

---

# 7. User Personas

**P1 — Routine appointment caller (~50% of inbound).**
Tamil-first, may switch to English for names and numbers. Wants "Dr X, tomorrow morning."
Success = booked and confirmed in under 3 minutes without hearing hold music.

**P2 — Information caller (~20%).**
"What time does the OP close?" "Is Dr Bala available on Saturday?" "Where is the lab?"
Success = a correct answer in one turn, then a clean goodbye.

**P3 — Existing patient with a modification (~10%).**
Reschedule or cancel. Needs identity confirmation by phone number + name.

**P4 — Out-of-scope caller (~15%).**
Billing, insurance, reports, admissions, complaints, asking for a specific person.
Success = a fast, graceful transfer to 122/123 with no dead air. **The AI's job here is to
get out of the way quickly** — a slow, confused AI is worse than the current IVR.

**P5 — Emergency / distressed caller (< 5%, but the highest-stakes path).**
Success = immediate priority transfer to a human, no data collection, no upsell to
appointment booking. See §14.4.

**P6 — Hospital receptionist (internal, ext 122/123).**
Receives transfers. Needs to know *why* the call was transferred and what was already
collected, without asking the patient to repeat themselves. See §14.5.

**P7 — Hospital administrator (internal).**
Needs the daily numbers: calls handled, bookings made, transfers, abandonment. See §22.

---
# 8. Call Flows

## 8.1 Happy path — appointment booking

```
Caller                     AI                          Systems
  |                         |                             |
  |--- call answered ------>|                             |
  |<-- greeting (pre-rec) --|  "Vanakkam, SPC Hospital.   |  <-- pre-recorded WAV,
  |                         |   How can I help you?"      |      0ms synthesis latency
  |                         |                             |
  |--- "Dr Kumar-a paakanum |                             |
  |     naalaikku" -------->|                             |
  |                         |-- STT (streaming) --------->|
  |                         |-- LLM: intent=BOOK,         |
  |                         |    doctor="Kumar",          |
  |                         |    date="tomorrow" -------->|
  |                         |-- GET /doctors?q=Kumar ---->|
  |                         |<-- 1 exact match -----------|
  |<-- "Dr. R. Kumar,       |                             |
  |     Cardiology, correct?|                             |
  |--- "aama" ------------->|                             |
  |                         |-- GET /slots?doctor=..&date |
  |                         |<-- [10:15, 10:45, 11:30] ---|
  |<-- "Naalaikku kaalai    |                             |  <-- AI reads at most 3 slots
  |     10:15, 10:45,       |                             |      NEVER invents a slot
  |     11:30 irukku." -----|                             |
  |--- "10:45" ------------>|                             |
  |                         |-- POST /slots/{id}/hold --->|  <-- 120s soft hold
  |                         |<-- hold_token --------------|
  |<-- "Ungaloda peyar?" ---|                             |
  |--- "Selvi Ramya" ------>|                             |
  |<-- "Vayasu?" -----------|                             |
  |--- "32" --------------->|                             |
  |<-- readback: name, age, |                             |
  |     doctor, slot -------|                             |
  |--- "aama, confirm" ---->|                             |
  |                         |-- POST /appointments ------>|  <-- idempotency_key = hold_token
  |                         |<-- 201 {ref: SPC-8341} -----|
  |<-- "Booking aayiduchu.  |                             |
  |     Reference SPC-8341. |                             |
  |     Naalaikku 10:45."---|                             |
  |<-- hangup --------------|                             |
```

**Non-negotiable rule:** the AI reads back name, doctor, date, and time before `POST
/appointments`. No booking is created without an explicit affirmative on the readback.

## 8.2 Out-of-scope path

```
Caller: "Enakku bill-la oru problem irukku."
   |
   v
Intent classifier -> BILLING (not in scope set)
   |
   v
AI: "Adhukku naan ungala hospital team-kitta connect panren.
     Konjam wait pannunga."         <-- pre-recorded WAV, not TTS
   |
   v
Release hold on any pending slot; write transfer record
   |
   v
SIP REFER to ring group [122, 123]
   |
   +-- accepted -> AI leg drops, caller talks to human. Session closed, state flushed.
   |
   +-- both busy -> COM1PBX existing queue behaviour (unchanged from today)
   |
   +-- REFER rejected / no route -> fall back to bridged transfer (§14.3)
```

**Latency target for this path: under 4 seconds from the caller finishing their sentence
to ringing at 122/123.** P4 callers judge the whole system on this number.

## 8.3 Escape hatches — always available, in every state

| Trigger | Behaviour |
|---|---|
| Caller presses `0` (DTMF) at any point | Immediate transfer to 122/123. No confirmation, no question. |
| Caller says "receptionist" / "human" / "manushan" / "yaaraavadhu" | Immediate transfer. |
| Emergency phrase list matches (§14.4) | **Priority** transfer, bypasses all queuing logic. No data collection. |
| Two consecutive STT turns produce no usable transcript | "Enakku kekkala. Naan connect panren." -> transfer. |
| Any state exceeds 90 seconds | Transfer. |
| Total call exceeds 6 minutes | Transfer. |
| Three consecutive silence timeouts (§20) | Transfer. |

**Design principle: the AI must be easier to escape than to fight.**

## 8.4 Call state machine

```
                 ┌──────────┐
                 │ GREETING │  (pre-recorded)
                 └────┬─────┘
                      v
              ┌───────────────┐
              │ INTENT_DETECT │◄──────────┐
              └───┬───┬───┬───┘           │
       BOOK       │   │   │  FAQ          │ (topic change)
        ┌─────────┘   │   └──────────┐    │
        v             │              v    │
 ┌──────────────┐     │        ┌──────────┴───┐
 │ IDENTIFY_DOC │     │        │ ANSWER_FAQ   │
 └──────┬───────┘     │        └──────┬───────┘
        v             │               v
 ┌──────────────┐     │        ┌──────────────┐
 │ OFFER_SLOTS  │     │        │ ANYTHING_ELSE│
 └──────┬───────┘     │        └──────┬───────┘
        v             │               │
 ┌──────────────┐     │               │
 │ HOLD_SLOT    │     │               │
 └──────┬───────┘     │               │
        v             │               │
 ┌──────────────┐     │               │
 │COLLECT_DETAIL│     │               │
 └──────┬───────┘     │               │
        v             │               │
 ┌──────────────┐     │               │
 │ CONFIRM      │     │               │
 └──────┬───────┘     │               │
        v             │               │
 ┌──────────────┐     │               │
 │ BOOK         │     │               │
 └──────┬───────┘     │               │
        └─────────────┴───────────────┤
                                      v
                              ┌──────────────┐
                              │     END      │
                              └──────────────┘

   ANY STATE ──(out of scope / escape / failure / timeout)──► ┌────────────┐
                                                              │ TRANSFERRING│
                                                              └─────┬───────┘
                                                                    v
                                                              ┌──────────┐
                                                              │TRANSFERRED│
                                                              └──────────┘
```

Every state has exactly two exits beyond its happy path: `TRANSFERRING` and `END`. There is
no state from which the caller cannot reach a human.

---

# 9. Telephony Architecture

## 9.1 Choosing the integration option

| Option | What it is | Verdict |
|---|---|---|
| **A — Generic "SIP"** | Too vague to evaluate; SIP is the transport, not the topology | Restated as B/C |
| **B — AI as a SIP extension** | The AI registers as extension e.g. `700`; the inbound route or AA points to it | **REJECTED.** An extension carries a busy state and a default `call-limit` of 1. This is the exact configuration that produces the "Caller B waits" failure in §0.1. Workable only as N separate registrations (700–724) behind a ring group — which is ugly, fragile, and needs 25 SIP accounts. |
| **C — AI as a SIP trunk / peer** | COM1PBX has a static-IP SIP trunk pointed at the AI box on the LAN; the inbound route sends calls to that trunk | **RECOMMENDED.** No busy state. Configurable channel limit. Native failover on 5xx/timeout. One configuration object. Same mechanism the PBX already uses for the telco. |
| **D — HTTP / webhook API** | Call control via REST | **REJECTED for media. Optional for control.** HTTP cannot carry real-time bidirectional audio — it has no jitter buffer, no packet-loss concealment, no sub-100ms delivery guarantee. If COM1PBX exposes an event webhook or AMI-style API (Q-12), use it for *supplementary call control and CDR correlation only* — never for audio. |

**Decision: Option C.** The AI is a SIP peer named `AI-GATEWAY`, on the hospital LAN,
static IP, channel limit 25, IP-ACL restricted to the PBX's address.

## 9.2 The four layers, kept separate

This separation is the thing most voice-AI projects get wrong.

| Layer | Protocol / mechanism | Component | Never does |
|---|---|---|---|
| **Signalling** | SIP (INVITE, ACK, BYE, REFER) over UDP/TCP on the LAN | Asterisk on the AI box | Touch audio samples or business data |
| **Media transport** | RTP, G.711 a-law, 8 kHz, 20 ms packets | Asterisk (jitter buffer, PLC, transcode) | Make decisions |
| **Call control** | AudioSocket (TCP) + internal REST | Python app | Parse SIP |
| **Business logic** | HTTPS / SQL | Python app + Booking API + Postgres | Know that SIP exists |

## 9.3 Why Asterisk is on the AI box — and why that is not "replacing the PBX"

Something must terminate SIP signalling and RTP media. FastAPI cannot: Python has no
production-grade SIP stack, no RTP jitter buffer, and no packet-loss concealment. The
options are (a) write a SIP stack — months of work, guaranteed bugs; (b) buy a managed SIP
platform such as LiveKit or Twilio — rejected in §20; or (c) run a well-understood open-
source SIP endpoint in a single-purpose role.

**Asterisk here is a media gateway, roughly 40 lines of dialplan.** It has no extensions,
no voicemail, no IVR, no queues, no users, and no PSTN trunks. It accepts calls from one
peer (COM1PBX), hands raw audio to Python over a TCP socket, and executes transfers when
Python tells it to. It is a protocol adapter, in the same sense that a database driver is
not a database.

The dialplan is essentially this:

```
[from-com1pbx]
exten => _X.,1,NoOp(Inbound from COM1PBX, DID=${EXTEN})
 same  =>     n,Set(CHANNEL(hangup_handler_push)=ai-cleanup,s,1)
 same  =>     n,Answer()
 same  =>     n,Set(SESSION_ID=${UNIQUEID})
 same  =>     n,AudioSocket(${SESSION_ID},127.0.0.1:9092)
 same  =>     n,Hangup()

[ai-cleanup]
exten => s,1,NoOp(Session ${UNIQUEID} ended, cause ${HANGUPCAUSE})
```

`AudioSocket` streams 8 kHz signed-linear PCM in 20 ms frames over a plain TCP connection,
one connection per call, with the session UUID sent in the first frame. It is the simplest
Asterisk-to-application audio interface that exists, and it is exactly what this
architecture needs.

**Transfer** is executed by the Python app via Asterisk ARI (`POST /channels/{id}/redirect`)
or AMI `Redirect`, which causes Asterisk to send SIP REFER toward COM1PBX. See §14.

## 9.4 Codec and audio-format chain

```
 PSTN --G.711 a-law 8k--> COM1PBX --G.711 a-law 8k--> Asterisk
                                                         |
                                              decode to slin 8 kHz
                                                         |
                                            AudioSocket (PCM16 8 kHz)
                                                         |
                                                   Python app
                                            |                      |
                            upsample to 16 kHz              (VAD at 8 kHz*)
                                            |
                                    Sarvam STT WebSocket

 Sarvam TTS WebSocket --mulaw 8 kHz--> Python --decode to PCM16 8k--> AudioSocket
                                                         |
                                                     Asterisk
                                                         |
                                              encode G.711 a-law
                                                         |
                                                     COM1PBX --> PSTN
```

Two format decisions worth spelling out:

- **STT: upsample 8 kHz to 16 kHz before sending to Sarvam.** Sarvam's streaming STT accepts
  8 kHz but its documentation states 16 kHz is recommended for optimal accuracy, and that
  8 kHz requires `sample_rate=8000` in *both* the connection URL and the transcribe
  parameters or quality degrades. Telephony audio genuinely contains no information above
  4 kHz, so upsampling adds no information — but it puts the model on its trained
  distribution. **Measure both during Gate 1 and pick empirically.** Do not assume.
- **TTS: request `mulaw` at 8 kHz directly from Sarvam.** Sarvam's streaming TTS supports
  mulaw and alaw at 8 kHz explicitly for telephony. This avoids a resample step on the hot
  path and halves the bytes on the wire versus PCM16.

---

# 10. AI Voice Architecture

## 10.1 The pipeline must stream. It cannot be sequential.

A strictly sequential `STT -> LLM -> TTS` pipeline produces this:

```
caller stops speaking
  |--- wait for full utterance, then transcribe .......... 900 ms
  |--- wait for complete LLM response .................... 1200 ms
  |--- wait for complete TTS synthesis ................... 800 ms
  |--- first audio reaches caller
  TOTAL: ~2.9 s of silence
```

Nearly 3 seconds of dead air per turn. Callers interpret this as a dropped call and hang
up or start talking over it. **Sequential is not acceptable for a phone receptionist.**

The streamed pipeline overlaps all three stages:

```
 caller speaking ──────────────────┐
   │                               │
   ├─ audio streams to STT continuously; partial transcripts arrive live
   │
   └─ VAD detects end-of-speech (400 ms trailing silence)
              │
              ├─ STT final transcript arrives ................. +120 ms
              │        (mostly already computed from partials)
              │
              ├─ LLM invoked, streaming ....... first token at +500 ms
              │
              ├─ first sentence boundary detected in LLM stream
              │        └─► pushed to TTS immediately (do NOT wait for full response)
              │
              ├─ TTS first audio chunk ......................... +250 ms
              │
              └─ audio reaches caller
        TOTAL: ~1.1-1.4 s
```

## 10.2 Latency budget (target p95 <= 1.5 s, hard ceiling 2.0 s)

| Stage | Budget | Notes |
|---|---|---|
| End-of-speech detection (VAD) | 400 ms | Tunable. Below ~300 ms the AI interrupts people mid-sentence; above ~600 ms it feels sluggish. Start at 400 ms, tune per §22 metrics. |
| STT final transcript after endpoint | 100–200 ms | Small because partials are already streaming |
| Backend tool call, when a state needs one | 0–250 ms | Local Postgres on the same LAN. **Budget 250 ms and enforce it as a hard timeout.** |
| LLM first token (Sarvam-105B) | 400–700 ms | The largest single component |
| LLM to first sentence boundary | +100–200 ms | ~10–15 tokens |
| TTS first audio byte (Bulbul WS) | ~250 ms | Sarvam documents sub-250 ms first-byte |
| Network (LAN) + Asterisk jitter buffer | 40–60 ms | Negligible on-prem; would be 100–200 ms if the AI were in a cloud region |
| **Total** | **~1.1–1.6 s** | |

**Two techniques buy back most of the remaining time, and both are mandatory:**

1. **Pre-recorded audio for every fixed utterance.** The greeting, "one moment please",
   the pre-transfer message, and all error messages are WAV files synthesised once at build
   time and stored on disk. Zero latency, zero cost, zero failure mode, and they still work
   when Sarvam is down. **This is the single highest-value optimisation in the document.**
2. **Filler audio on slow turns.** If no TTS audio has been produced 700 ms after
   end-of-speech, play a short pre-recorded acknowledgement ("mm", "okay", "one second")
   while the real response is still being generated. Perceived latency drops far more than
   measured latency.

## 10.3 Barge-in

Barge-in is non-optional. Indian callers interrupt constantly, and an AI that talks over
an interruption is unusable.

```
   TTS audio playing to caller
            │
   inbound RTP simultaneously fed to VAD (always on, never gated on TTS state)
            │
   speech energy sustained for >= 200 ms
            │
            ├─► STOP writing TTS audio to AudioSocket immediately
            ├─► FLUSH the outbound audio buffer (target < 150 ms stop time)
            ├─► CANCEL the in-flight LLM stream (drop the task; discard partial tokens)
            ├─► CLOSE and reopen the Sarvam TTS WebSocket
            └─► TRUNCATE the assistant message in conversation history to what the
                caller actually heard, and append "[interrupted]"
```

Two details that are easy to get wrong:

- **Sarvam's TTS WebSocket has no server-side cancel.** Their documentation is explicit:
  handle interruptions client-side by stopping playback, closing the socket, and opening a
  fresh one. So barge-in costs a WebSocket reconnect (~50–150 ms). Keep one warm spare TTS
  connection per active call to absorb this. Budget the extra connections against the
  Sarvam TTS concurrency limit (§12.4).
- **History truncation is a correctness requirement, not a nicety.** If the LLM's context
  says it offered slots 10:15, 10:45 and 11:30 but the caller only heard "10:15" before
  interrupting, the model will reason about facts the caller never received. Truncate to
  the audio actually delivered.

**Echo risk:** the caller's line carries some of the AI's own audio back (hybrid echo on
PSTN). Enable Asterisk's echo canceller on the channel and require sustained speech
(200 ms), not a single energy spike, before declaring barge-in. Validate on real PSTN calls
during Gate 1 — this cannot be tested over a LAN-only softphone.

## 10.4 Component choice: hand-rolled vs Pipecat

Sarvam's own reference integration uses **Pipecat**, which supplies the frame-based
pipeline, VAD integration, interruption handling, resampling, and first-class
`SarvamSTTService` / `SarvamLLMService` / `SarvamTTSService` plugins.

| | Hand-rolled asyncio | Pipecat |
|---|---|---|
| Barge-in, turn-taking, resampling | You write and debug it | Provided, battle-tested |
| Sarvam integration | Raw WebSockets | Maintained plugins |
| Transport | You write AudioSocket glue | You still write AudioSocket glue (its built-in transports are Twilio/WebRTC) |
| Control over the FSM | Total | Total — the FSM sits outside the pipeline either way |
| Dependency weight | Low | Moderate, actively maintained |

**Recommendation: use Pipecat for the media pipeline, with a custom AudioSocket transport
(~150 lines), and keep the appointment FSM entirely outside it.** This is not a violation
of "no unnecessary frameworks" — turn-taking and interruption handling are genuinely hard,
Sarvam maintains the plugins, and the alternative is reimplementing the same logic worse.
The framework this PRD refuses is the *agent* framework (§21), not the audio plumbing.

If Pipecat's AudioSocket integration proves awkward during Gate 1, hand-rolling the
pipeline is a viable fallback at roughly two additional weeks of effort. Decide at Gate 1,
not now.

---

# 11. COM1PBX Integration

> Everything in this section is contingent on §0.5. Do not begin implementation until
> Q-2, Q-5, Q-7, Q-10, and Q-11 are answered in writing.

## 11.1 Configuration to request from COM1PBX

**1. A SIP trunk/peer named `AI-GATEWAY`:**

| Setting | Value | Why |
|---|---|---|
| Type | SIP peer / trunk, static IP (no registration) | Peers have no busy state; extensions do |
| Host | `192.168.x.x` (AI box, static LAN IP) | On-LAN keeps media off the internet entirely |
| Port | 5060 UDP (or 5061 TLS if supported) | |
| Max channels | **25** | The setting that makes concurrent AI calls possible at all (Q-5) |
| Codecs | `alaw` only (or `alaw,ulaw`) | Avoid G.729 licensing and transcode CPU |
| DTMF | RFC 2833 | Needed for the `0` escape hatch |
| Qualify | Yes, 30s | So the PBX knows the AI is down *before* routing a call to it |
| IP ACL | Accept only from COM1PBX's IP | §21 |
| Direct media | **No** | Media must flow through Asterisk |

**2. Inbound route change, staged:**

| Stage | Route target | Purpose |
|---|---|---|
| Today | `AA_New_IN-SLM` -> extensions | Baseline |
| Pilot | AA option "1 for appointments" -> `AI-GATEWAY`; everything else unchanged | Opt-in exposure, trivially reversible. **Start here.** |
| Ramp | One DID (0427-2312020) -> `AI-GATEWAY` directly | Half the traffic |
| Full | Both DIDs -> `AI-GATEWAY` | Only after the metrics in §5 hold for two weeks |

**3. Failover on the inbound route (Q-10) — mandatory before any traffic is sent:**

```
   Destination: AI-GATEWAY trunk
   On unreachable / 5xx / no answer within 4 seconds
        -> Ring group [122, 123]
        -> then existing queue / voicemail behaviour
```

If COM1PBX cannot do route-level failover, `qualify` plus a manual route switch is the
minimum acceptable substitute, and the runbook must cover it. **Do not go live without one
of these.**

## 11.2 Transfer mechanism — preference order

| Preference | Mechanism | Requires |
|---|---|---|
| 1 | **SIP REFER** from Asterisk to COM1PBX, Refer-To `sip:122@com1pbx` | COM1PBX honours REFER from an external peer (Q-11). Cleanest: the AI leg tears down and the trunk channel toward the AI is freed. |
| 2 | **Bridged transfer** — Asterisk dials 122/123 back over the trunk and bridges the two legs, then exits | Nothing special. Costs a second trunk channel toward the PBX for the whole call and keeps the AI box in the media path. Works everywhere. |
| 3 | **DTMF feature code** — Asterisk sends the PBX's own attended-transfer code | Feature codes documented by COM1PBX. Brittle; last resort. |

**Design for 1, implement 2 as the fallback, and test both at Gate 1.** Note that
preference 2 doubles channel consumption during transfers — factor it into §16.

## 11.3 What is deliberately NOT changed on COM1PBX

- Extensions 122/123 and their existing client console
- Existing call recording configuration and file naming
- Department extensions (111, 244, 600, 616, 666) and named targets
- Outbound routing and dial plans
- The existing queue and voicemail behaviour

**The AI adds one trunk and one route branch. That is the whole footprint on the PBX.**

---

# 12. Sarvam Integration

## 12.1 Component map

| Pipeline stage | Sarvam product | Model | Interface |
|---|---|---|---|
| Speech recognition | Streaming STT | `saaras:v3` (or `saaras:v3-realtime`) | WebSocket, persistent per call |
| Conversational reasoning | Chat completion | `sarvam-105b` | HTTPS, streaming, per turn |
| Speech synthesis | Streaming TTS | `bulbul:v2` (MVP) / `bulbul:v3` (evaluate) | WebSocket, persistent per call |

Sarvam covers all three stages for Indian languages under a single API key. **No second LLM
provider is introduced** (§22).

## 12.2 STT configuration

```python
# One WebSocket per call, opened at call start, closed at hangup.
{
  "model": "saaras:v3",
  "language": "ta-IN",              # see 12.3
  "mode": "codemix",                # critical for Tanglish
  "sample_rate": 16000,             # also set in the connection URL
  "high_vad_sensitivity": False,    # tune per 10.2
}
```

- **`mode: "codemix"` is the setting that makes this work in Salem.** Sarvam's Saaras v3
  supports `transcribe`, `translate`, `verbatim`, `translit`, and `codemix`. Tanglish
  ("Dr Kumar-a naalaikku paakanum") is exactly what `codemix` is for. `transcribe` mode
  will mangle it.
- **Sample rate must be declared in both the connection URL and the transcribe parameters.**
  Sarvam's docs warn explicitly that mismatching them degrades quality silently. This is a
  known footgun — assert it in code.
- Only WAV and raw PCM (`pcm_s16le`) are accepted on the streaming WebSocket. MP3/AAC/OGG
  are not.
- **SDK limitation to plan around:** the Python SDK's `transcribe()` is limited to
  `audio/wav`; raw PCM requires a hand-rolled WebSocket client. Use raw WebSockets (or
  Pipecat's plugin) rather than fighting the SDK.

## 12.3 Language strategy

The realistic caller mix is Tamil-dominant with English medical and numeric terms, plus
some Hindi. Handle it in this order:

1. **Default to `ta-IN` + `codemix`.** Salem is Tamil-first. Do not run language detection
   on the first utterance — it costs a round trip and gets the greeting wrong.
2. **The greeting is bilingual and pre-recorded:** "Vanakkam, SPC Hospital. Tamil-la
   pesalaam, or you can speak in English."
3. **Switch on evidence, not on guesswork.** If two consecutive turns come back with
   confidence below threshold, or the transcript is overwhelmingly English, reconfigure the
   live STT session (`saaras:v3-realtime` supports mid-call reconfiguration) and switch the
   TTS speaker to the English voice.
4. **The LLM system prompt instructs: reply in the language the caller last used.** One
   instruction, no routing logic.
5. **Numbers, dates, and doctor names are always confirmed by readback.** This is where
   code-mixed ASR fails most often, and readback catches it deterministically rather than
   probabilistically.

## 12.4 Rate limits — the real constraint, and it binds

Sarvam's published limits (per **account**, shared across all API keys):

| API | Starter | Pro | Business |
|---|---|---|---|
| STT WebSocket streaming | **20 concurrent** | 100 concurrent | 100 concurrent |
| TTS WebSocket streaming | 60 concurrent | 200 concurrent | 1,000 concurrent |
| TTS WS — bulbul:v3 | 30 concurrent | — | — |
| Chat completion (default models) | 60 req/min | 200 req/min | 1,000 req/min |
| **Chat completion — Sarvam-105B** | **40 req/min** | **60 req/min** | **120 req/min** |
| STT REST realtime | 60 req/min | 100 req/min | 4,000 req/min |

**Three findings that change the plan:**

1. **Starter's 20 concurrent STT WebSockets is below the 25-call target.** 25 concurrent
   calls need at minimum the **Pro** plan. Non-negotiable.
2. **Sarvam-105B LLM rate limits bind before anything else.** At 25 concurrent calls with
   ~8 LLM turns per 3-minute call:
   `25 calls x 8 turns / 3 min = ~67 requests/min` — which **exceeds Pro's 60 rpm for
   Sarvam-105B** and needs **Business (120 rpm)**. At the realistic peak from §0.6 (4
   concurrent calls) it is ~11 rpm and Starter suffices. **This is the sharpest argument for
   sizing from the CDR rather than from the 25 target.**
3. **Sarvam's docs note the WebSocket limiter "reacts to how fast new connections are
   opened."** Burst connection attempts can be rejected *below* the stated concurrent
   ceiling. Since barge-in forces TTS reconnects, a busy hour with lots of interruptions is
   exactly the burst pattern that trips this. **Mitigation: a small per-process connection
   pool with a token-bucket limiter on new-connection rate, plus one warm spare TTS socket
   per active call.**

**Plan recommendation: start on Pro. Move to Business only when the LLM request-rate metric
(§22) crosses 80% of the Pro ceiling for a sustained 5 minutes.**

## 12.5 Context and token management

Phone conversations are short; the risk is not context length but latency growth and cost
from resending history.

| Rule | Value | Reason |
|---|---|---|
| System prompt | ~600–900 tokens, **static and identical across all calls** | Static prefix = cache hit. Sarvam charges ~1/3 for cached input tokens. |
| History window | Last 10 turns, verbatim | ~8-turn calls rarely exceed it |
| Overflow | Summarise turns 1–N into one line, keep last 6 verbatim | Rare |
| Structured state | Passed as a compact JSON block, **not** as conversation history | The FSM owns the truth; the LLM only phrases it |
| Max output tokens | **80** | A receptionist speaks in one or two sentences. Capping output is the cheapest latency control available. |
| Hard turn timeout | 2.5 s | Then filler audio, then the degradation path (§20) |

**Prompt caching is worth real money here.** Sarvam-105B cached input is ₹10.98/M vs
₹29.28/M — a 62% saving on the largest token component. Keep the system prompt
byte-identical across calls; put all per-call variation *after* it.

## 12.6 Failure handling per Sarvam service

| Failure | Detection | Response |
|---|---|---|
| STT WebSocket drops mid-call | `on_close` | Reconnect once (< 500 ms), replay the last 2 s from a ring buffer. Second failure -> transfer. |
| STT returns empty / garbage twice | Transcript heuristics | Pre-recorded "Enakku sariyaa kekkala" -> transfer |
| LLM 429 rate limit | HTTP 429 | Retry once after 300 ms jitter; on second 429, transfer. **Emit a metric — this is the signal to upgrade plan tier.** |
| LLM 5xx or timeout > 2.5 s | Timeout | Filler audio, one retry; on failure, transfer |
| TTS WebSocket fails | `on_close` / no audio in 800 ms | Reconnect from the warm spare; if that fails, play pre-recorded fallback and transfer |
| Sarvam entirely unreachable | 3 consecutive failures across services within 60 s | **Circuit breaker opens.** All *new* calls are refused at the SIP layer with a 503 so COM1PBX fails the route over to 122/123 (§11.1). In-flight calls transfer. Breaker retries every 30 s. |

**The circuit breaker returning 503 is the key behaviour.** It converts an AI outage into
ordinary PBX routing rather than into dead air.

---

# 13. Appointment Booking Workflow

## 13.1 The core rule

> **The LLM never invents, computes, or asserts availability. It reads back what the
> Booking API returned, and nothing else.**

Mechanically enforced three ways:

1. **Availability is only ever supplied via tool-call results**, never inferred from the
   system prompt or training data.
2. **The slot IDs the LLM may offer are validated against the API response.** If the model
   emits a time not in the returned set, the turn is discarded and a repair prompt is
   issued. This check is a dozen lines of code and it eliminates the highest-severity
   failure mode in the system.
3. **The final `POST /appointments` is executed by the FSM, not by the LLM.** The model
   signals intent; the state machine performs the write.

## 13.2 The booking sequence with the failure branches

```
 IDENTIFY_DOCTOR
   GET /doctors?q={name}
   ├─ 1 exact match  ──► confirm by readback ──► OFFER_SLOTS
   ├─ 2-3 matches    ──► disambiguate by department ("Cardiology or Ortho?")
   ├─ >3 matches     ──► ask for department first
   └─ 0 matches      ──► offer department-based search; 2 failures ──► TRANSFER

 OFFER_SLOTS
   GET /availability?doctor_id=..&date_from=..&date_to=..
   ├─ slots exist    ──► offer at most 3, nearest first
   ├─ none that day  ──► offer the next 2 days with availability
   └─ none in 14 days ──► TRANSFER ("our team will call you back")

 HOLD_SLOT
   POST /slots/{slot_id}/hold   -> {hold_token, expires_at: now+120s}
   ├─ 200 ──► COLLECT_DETAILS
   └─ 409 (taken while talking) ──► "Adhu just now book aayiduchu" ──► back to OFFER_SLOTS

 COLLECT_DETAILS
   Required: patient_name, age, phone (default = caller ID, confirmed)
   Optional: reason_for_visit (free text, NEVER interpreted)
   ├─ each field confirmed by readback
   └─ 2 failed attempts on any field ──► TRANSFER

 CONFIRM
   Full readback: "Dr Kumar, naalaikku kaalai 10:45, Selvi Ramya, 32 vayasu. Sari-yaa?"
   ├─ affirmative ──► BOOK
   ├─ correction  ──► back to the specific field
   └─ 2 failures  ──► TRANSFER

 BOOK
   POST /appointments  (Idempotency-Key = hold_token)
   ├─ 201 ──► read out reference number, twice, digit by digit ──► END
   ├─ 409 ──► hold expired; re-check availability ──► OFFER_SLOTS
   └─ 5xx / timeout ──► ONE retry with the same idempotency key
                        ──► on failure: "Booking system-la problem. Naan team-kitta
                            connect panren." ──► TRANSFER with full context attached
```

**The 120-second soft hold is what makes 25 concurrent callers safe.** Without it, two
callers being offered the same 10:45 slot will both reach `CONFIRM` and one will fail after
having been told it was available — the worst possible caller experience. The hold moves
the collision to the point where it can be handled gracefully.

## 13.3 Double-booking prevention

Application-level checks are insufficient under concurrency. Enforce it in the database:

```sql
-- The slot row is the lock. One row per bookable slot.
CREATE UNIQUE INDEX uniq_active_slot
  ON appointments (slot_id)
  WHERE status IN ('held', 'booked');
```

Combined with `SELECT ... FOR UPDATE` on the slot row inside the booking transaction, a
double-booking becomes a constraint violation the application handles as a 409, not a data
corruption. **The database is the arbiter. The LLM is not, and neither is the FSM.**

## 13.4 What the AI must never do in this flow

- Never state that a doctor "is usually available" or "normally sees patients on Tuesdays."
- Never estimate a wait time.
- Never book without an explicit affirmative on the full readback.
- Never accept a reason-for-visit as a clinical input, offer advice on it, or route based
  on it. It is stored verbatim as a string for the receptionist to read.
- Never proceed past two failed attempts on any field. Transfer instead.

---
# 14. Human Handoff

## 14.1 Blind vs warm transfer

| | Blind (SIP REFER) | Warm / bridged |
|---|---|---|
| AI stays on the call | No — it drops immediately | Yes, until the human answers |
| Trunk channels consumed | Back to 1 (the caller's inbound leg) | 2 for the whole call |
| Caller experience if nobody answers | Falls into COM1PBX's existing queue — identical to today | AI can come back and apologise |
| Context handoff to the human | Via screen-pop only | Possible via whisper announcement |
| Complexity | Low | Moderate |

**Decision: blind transfer (SIP REFER) is the default for all transfers.**

Rationale: it restores exactly the behaviour the hospital has today, frees the AI-side
channel immediately, and removes the AI from the media path — meaning an AI crash during a
transferred call cannot drop the caller. Warm transfer's main advantage (the AI apologising
when nobody picks up) is not worth doubling channel consumption during exactly the busy
periods when channels are scarcest.

**One exception: emergency transfers (§14.4) use warm/bridged transfer**, so the AI can
detect a non-answer and immediately try the next destination rather than depositing a
distressed caller in a queue.

## 14.2 Transfer sequence

```
 FSM enters TRANSFERRING
   │
   ├─ 1. Release any held slot          POST /slots/{id}/release
   ├─ 2. Write the transfer record      (call_id, reason, collected context, timestamp)
   ├─ 3. Push the screen-pop            POST /internal/screenpop  (§14.5)
   ├─ 4. Play pre-recorded message      "Naan ungala hospital team-kitta connect panren."
   │                                     <-- WAV from disk. NEVER TTS. It must play even
   │                                         if Sarvam is the reason for the transfer.
   ├─ 5. Execute transfer               ARI redirect -> SIP REFER to ring group [122,123]
   │
   ├─ 6a. REFER accepted (202 + NOTIFY 200) ──► AI leg drops. Session -> TRANSFERRED.
   │
   ├─ 6b. REFER rejected / not implemented ──► fall back to bridged transfer (§11.2 #2)
   │
   └─ 6c. Bridged transfer, nobody answers in 20s
             ├─ try the other extension
             └─ both unavailable ──► §14.6
```

## 14.3 What happens when 122 and 123 are both busy

**Critical decision: the AI must NOT try to be cleverer than the PBX here.**

The correct behaviour is: **REFER to a ring group or queue containing both 122 and 123, and
let COM1PBX apply the queue behaviour it already applies today.** The AI does not implement
its own retry ladder against individual extensions, because:

- The PBX has live presence/busy state for those extensions; the AI does not.
- The hospital's existing hold music, position announcements, and voicemail already exist
  and staff already understand them.
- An AI-side retry loop is a new failure mode with no compensating benefit.

**The caller ends up exactly where they would have ended up without the AI. That is the
correct outcome, not a compromise** — the AI's value is the calls it *absorbs*, not the
calls it hands over.

**However** — Q-6 and Q-10 must confirm what that queue behaviour actually is. If today's
answer is "rings busy and the caller hears a fast-busy tone," that is unacceptable for
escalated calls and the hospital must configure a queue or voicemail before go-live. This
is a **hospital operations decision**, listed in §29.

## 14.4 Emergency path

Emergency detection is a **deterministic phrase-list match on the STT transcript, evaluated
before the LLM sees the turn.** It is not an LLM classification, because a rate-limited or
slow LLM must never delay an emergency.

```
 STT final transcript
      │
      ├─► regex/phrase match against the clinical emergency list  ── MATCH ──┐
      │   (Tamil + English + Tanglish variants; owned and signed off by      │
      │    the hospital's clinical lead, versioned in the repo)              │
      │                                                                      v
      └─► normal LLM turn                                    PRIORITY TRANSFER
                                                              - stop all TTS immediately
                                                              - play emergency WAV
                                                              - WARM transfer to 122
                                                              - if no answer in 8s -> 123
                                                              - if no answer in 8s ->
                                                                hospital emergency ext
                                                                (TBD - §29)
                                                              - collect NOTHING
                                                              - log at CRITICAL, alert
```

**Deliberately biased toward false positives.** A wrongly escalated non-emergency costs a
receptionist thirty seconds. A missed emergency is a different category of failure.

## 14.5 Passing context to the human

SIP headers do not reach a human's ear, and asking the caller to repeat themselves defeats
much of the value. Two mechanisms:

1. **Screen-pop (primary).** On transfer, `POST /internal/screenpop` writes a record keyed
   by caller number to a small internal web page the receptionists keep open. It shows:
   caller number, transfer reason, detected intent, doctor discussed, slot under
   consideration, name/age collected, and the last 3 conversation turns.
   *Dependency: this must coexist with the receptionists' existing client console. Confirm
   with hospital IT — §29.*
2. **Transcript record (always).** Every transfer writes a full row to `transfers` and
   `call_turns`, available in the admin dashboard even if the screen-pop is missed.

**A whisper announcement to the receptionist before bridging is explicitly deferred to
Phase 2.** It adds 2–3 seconds to every transfer and only works on bridged transfers.

## 14.6 Transfer failure handling

| Failure | Behaviour |
|---|---|
| REFER returns 4xx/5xx | Fall back to bridged transfer. Log. |
| Bridged transfer: both extensions ring out (20s) | Play pre-recorded: "Ellarum busy-a irukkanga. Ungaloda number-a note pannitten, hospital-la irundhu call panruvanga." Write a callback record. Hang up gracefully. **Never leave silence.** |
| COM1PBX unreachable during transfer | Same as above — callback record + graceful close. Alert at CRITICAL. |
| Caller hangs up mid-transfer | Normal cleanup. Log disposition `abandoned_during_transfer`. This metric matters: a rising value means transfers are too slow. |

---

# 15. Concurrency Model

## 15.1 Isolation — how each call stays separate

```
   COM1PBX
      │  25 independent SIP dialogs (distinct Call-ID, distinct RTP port pairs)
      ▼
   Asterisk
      │  25 independent channels, one AudioSocket TCP connection each
      ▼
   Python process (single, asyncio)
      │
      ├── CallSession(id=call_a1b2)  ── owns: audio buffers, STT WS, TTS WS,
      │      asyncio.Task tree              FSM state, conversation history,
      │                                      DB connection (borrowed per query)
      ├── CallSession(id=call_c3d4)  ── owns: ... its own of everything ...
      ├── ...
      └── CallSession(id=call_y9z0)
```

**The isolation guarantee rests on one rule: every piece of per-call data is an instance
attribute on `CallSession`. There are no module-level mutable variables in the
conversation path.** That is the whole mechanism, and it is enforced by review and by a
lint rule, not by hope.

The cross-contamination bugs that actually happen in voice AI systems are:

| Bug | Prevention |
|---|---|
| Shared LLM client holding conversation state | The Sarvam client is stateless; history is passed per request from `session.history` |
| A module-level `current_call` global | Banned. `CallSession` is passed explicitly through every function. |
| Shared mutable default arguments (`def f(history=[])`) | Lint rule (`B006`), enforced in CI |
| Audio buffer reuse across sessions | Buffers are allocated per session and never pooled |
| A cached prompt containing a previous caller's data | The system prompt is static; per-call data is appended after it, never interpolated into it |
| A `contextvar` leaking across `asyncio.Task` boundaries | Session is a parameter, not a contextvar |

## 15.2 Does one instance handle 25 calls?

**Yes, comfortably.** Per-call work is almost entirely I/O wait on WebSockets, not
computation. Actual CPU per call:

| Work | Cost per call |
|---|---|
| Silero VAD (ONNX, 30 ms frames) | ~1 ms per 30 ms frame -> **~3% of one core** |
| Resampling 8k<->16k | negligible |
| mulaw decode | negligible |
| WebSocket framing + asyncio scheduling | ~2–5% of a core |
| **Total** | **~0.08–0.15 core per call** |

25 calls -> **2–4 cores of real work.** A single 8 vCPU box runs it with better than 2x
headroom (§24).

**Important caveat: Python's GIL means one process, not one thread.** Silero VAD releases
the GIL inside ONNX Runtime, so VAD does not serialise, but pure-Python audio handling does.
At 25 calls this is fine. If load ever tripled, run 2–4 processes behind separate
AudioSocket ports with Asterisk round-robining between them — **not** threads.

## 15.3 The 25-call resource ledger

| Resource | Per call | At 25 calls | Provisioned | Headroom |
|---|---|---|---|---|
| Asterisk channels | 1 | 25 | 100 (default) | 4x |
| AudioSocket TCP connections | 1 | 25 | 1024 fds | 40x |
| Sarvam STT WebSockets | 1 | 25 | Pro: 100 | 4x |
| Sarvam TTS WebSockets | 1 + 1 warm spare | 50 | Pro: 200 | 4x |
| Sarvam LLM requests/min | ~2.7 | **~67 rpm** | Pro (105B): **60 rpm** | **NEGATIVE — see §12.4** |
| Postgres connections | 0 idle, borrowed per query | peak ~10 | pool of 20 | 2x |
| Redis connections | shared | 2–4 | 10 | — |
| asyncio tasks | ~6 | ~150 | — | fine |
| RAM | ~40–60 MB | ~1.5 GB | 16 GB | 10x |
| CPU | ~0.12 core | ~3 cores | 8 vCPU | 2.7x |
| Internet bandwidth | ~180 kbps | ~4.5 Mbps | 20 Mbps line | 4x |
| LAN bandwidth (SIP/RTP) | ~170 kbps | ~4.3 Mbps | 1 Gbps | huge |

**Exactly one row is red, and it is not infrastructure.** The Sarvam-105B LLM rate limit is
the binding constraint at the 25-call target. Everything else has multiples of headroom.
This is why §24 recommends provisioning for measured load and treating 25 as a software
ceiling.

## 15.4 Session cleanup

Cleanup runs on **every** termination path — normal hangup, caller abandon, transfer,
crash, and timeout — via a single `finally` block plus an Asterisk hangup handler:

```
 on_call_end(session):
   1. Cancel all child asyncio tasks (STT reader, TTS writer, LLM stream)  -- with timeout
   2. Close the STT WebSocket
   3. Close the TTS WebSocket(s), including the warm spare
   4. Release any held appointment slot          <-- MOST IMPORTANT: a leaked hold
                                                     blocks a real slot for 120s
   5. Flush call_turns and the call record to Postgres
   6. Delete the session from the in-process registry and from Redis
   7. Emit the call_completed metric with the disposition
```

**Belt and braces:** an independent janitor task sweeps every 30 seconds for sessions with
no AudioSocket activity in 60 s and force-cleans them. Slot holds also carry a DB-side
`expires_at` so an orphaned hold self-releases even if the janitor is dead. **Never rely on
a single cleanup path in a system where the process can be killed mid-call.**

---

# 16. Call Waiting Model

## 16.1 Five independent capacity layers

These are routinely conflated. They are not the same thing, they have different limits, and
they fail differently:

```
 ┌──────────────────────────────────────────────────────────────────┐
 │ L1  TELCO TRUNK CHANNELS            limit: UNKNOWN (Gate 0)      │
 │     Exceeded -> caller hears busy from the network.              │
 │     The call never reaches COM1PBX and never appears in the CDR. │
 │     >> HARD CEILING ON EVERYTHING BELOW <<                       │
 ├──────────────────────────────────────────────────────────────────┤
 │ L2  COM1PBX CONCURRENT-CALL LICENCE  limit: UNKNOWN (Q-2, Q-9)   │
 │     Exceeded -> calls rejected or queued at the PBX.             │
 ├──────────────────────────────────────────────────────────────────┤
 │ L3  AI TRUNK CHANNEL LIMIT           limit: 25 (we set this)     │
 │     Exceeded -> PBX route failover fires -> 122/123. Graceful.   │
 ├──────────────────────────────────────────────────────────────────┤
 │ L4  AI APPLICATION CONCURRENCY       limit: ~75 (CPU-bound)      │
 │     Exceeded -> latency degrades for everyone. Worst failure     │
 │     mode of all, because it is silent. Admission control (§16.3) │
 ├──────────────────────────────────────────────────────────────────┤
 │ L5  SARVAM API CONCURRENCY           limit: STT 100 / LLM 60 rpm │
 │     Exceeded -> 429s -> per-call transfer to human.              │
 ├──────────────────────────────────────────────────────────────────┤
 │ L6  HUMAN EXTENSION CONCURRENCY      limit: 2 (122, 123)         │
 │     Exceeded -> existing PBX queue. Unchanged by this project.   │
 └──────────────────────────────────────────────────────────────────┘
```

## 16.2 Where the bottleneck actually sits

| Scenario | Binding layer | Symptom |
|---|---|---|
| 8-channel trunk, 25-call design | **L1** | 9th caller gets network busy. The AI is irrelevant. |
| E1 PRI (30ch), Sarvam Starter | **L5 (STT: 20 concurrent)** | 21st AI call fails STT -> transfers. |
| E1 PRI, Sarvam Pro, 25 concurrent | **L5 (LLM: 60 rpm)** | Sporadic 429s -> sporadic transfers under peak load |
| E1 PRI, Sarvam Business | **L6** | The AI absorbs everything it can; overflow to 2 humans. **This is the intended steady state.** |
| Anything, AI box undersized | **L4** | Latency creeps past 2 s for *all* calls. Silent and insidious. |

**L4 is the dangerous one** because it degrades everyone simultaneously instead of failing
one call cleanly. Hence admission control.

## 16.3 Admission control — refuse rather than degrade

```
 New call arrives at Asterisk
   │
   ├─ active_sessions >= 25 ?
   │     └─ YES -> return SIP 503 Service Unavailable
   │               -> COM1PBX route failover fires -> 122/123
   │               -> emit metric: ai_call_rejected{reason="at_capacity"}
   │
   ├─ Sarvam circuit breaker OPEN ?
   │     └─ YES -> SIP 503 -> same failover path
   │
   ├─ p95 turn latency over last 60s > 2.0s ?
   │     └─ YES -> SIP 503 (shed load before it becomes systemic)
   │
   └─ otherwise -> accept the call
```

**Refusing a call at the SIP layer is a *good* outcome.** COM1PBX handles it with the
routing logic it already has, and the caller reaches a human exactly as they do today. A
degraded AI conversation is strictly worse for the caller than no AI at all.

---

# 17. Session Management

## 17.1 Where state lives — and why Redis is barely needed

A phone call is inherently pinned to one process for its entire life: the AudioSocket TCP
connection terminates in exactly one process, and audio cannot be load-balanced mid-call.
**Therefore conversation state does not need a distributed store.**

| State | Lives in | Lifetime | Why there |
|---|---|---|---|
| Audio buffers, VAD state, WebSocket handles | **Process memory** (`CallSession`) | The call | Cannot be serialised; must be local |
| Conversation history, FSM state, collected fields | **Process memory** | The call | Written to Postgres at the end; nothing else needs it live |
| Live call registry (who is on which call, state, duration) | **Redis** (hash, 1 h TTL) | The call | Read by the dashboard and the janitor — genuinely cross-process |
| Slot holds | **Postgres** (with `expires_at`) | 120 s | Must be transactional and must survive a process crash |
| Booking idempotency keys | **Postgres** | 24 h | Must survive a retry after a crash |
| Rate-limit token buckets for Sarvam | **Redis** | seconds | Shared if more than one process runs |
| Call records, transcripts, appointments, audit log | **Postgres** | Per retention policy (§21.7) | System of record |

**Decision: Postgres is the system of record. Redis is used only for the live registry and
shared rate limiting. Conversation state stays in memory.**

Adding Redis to hold conversation history would buy nothing — if the owning process dies,
the call's media connection dies with it, and the caller must be re-handled regardless. It
would only add a serialisation cost on every turn, on the latency-critical path.

**Redis is technically optional for a single-process MVP.** Keep it: it costs almost
nothing, and it is the difference between a two-hour and a two-week change when a second
process becomes necessary.

## 17.2 Session identity

```python
session_id   = f"call_{asterisk_uniqueid}"     # one identifier end to end:
                                               # Asterisk UNIQUEID -> AudioSocket UUID
                                               # -> logs -> metrics -> DB -> COM1PBX CDR
```

**One ID across every layer.** This is what makes a production incident debuggable: given a
caller complaint and a timestamp, you can pull the PBX CDR row, the Asterisk channel log,
every application log line, every latency metric, the transcript, and the appointment — all
by the same key.

## 17.3 CallSession contents

```python
@dataclass
class CallSession:
    # identity
    session_id: str
    caller_number: str            # masked in all logs (§21.5)
    did: str                      # 2529500 or 2312020
    started_at: datetime

    # conversation
    state: CallState              # the FSM enum from §8.4
    history: list[Turn]           # last 10 turns
    language: str                 # "ta-IN" | "en-IN", may change mid-call

    # booking context
    doctor_id: int | None
    offered_slots: list[Slot]     # the ONLY slots the LLM may offer (§13.1)
    held_slot_id: int | None
    hold_token: str | None
    hold_expires_at: datetime | None
    patient_name: str | None
    patient_age: int | None
    reason_for_visit: str | None

    # transfer
    transfer_reason: TransferReason | None
    transfer_attempts: int

    # runtime handles (never serialised)
    stt_ws: WebSocket
    tts_ws: WebSocket
    tts_ws_spare: WebSocket
    audio_out: asyncio.Queue
    tasks: set[asyncio.Task]

    # metrics
    turn_count: int
    llm_calls: int
    last_activity_at: datetime
```

---

# 18. Data Model

```sql
-- ============ Reference data ============

CREATE TABLE departments (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    name_ta       TEXT,                       -- Tamil name, for TTS
    aliases       TEXT[] DEFAULT '{}',        -- "heart", "cardio", "idhaya"
    pbx_extension TEXT,                       -- for future department routing
    active        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE doctors (
    id             SERIAL PRIMARY KEY,
    full_name      TEXT NOT NULL,
    display_name   TEXT NOT NULL,             -- what the TTS actually says
    name_ta        TEXT,
    aliases        TEXT[] DEFAULT '{}',       -- ASR variants: "kumaar", "dr kumar"
    department_id  INT NOT NULL REFERENCES departments(id),
    qualification  TEXT,
    consult_minutes INT NOT NULL DEFAULT 15,
    active         BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX doctors_alias_gin ON doctors USING GIN (aliases);

-- Recurring weekly schedule
CREATE TABLE doctor_schedules (
    id          SERIAL PRIMARY KEY,
    doctor_id   INT NOT NULL REFERENCES doctors(id),
    weekday     SMALLINT NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time  TIME NOT NULL,
    end_time    TIME NOT NULL,
    valid_from  DATE NOT NULL,
    valid_to    DATE
);

-- Leave, camps, surgery blocks. Overrides the recurring schedule.
CREATE TABLE schedule_exceptions (
    id          SERIAL PRIMARY KEY,
    doctor_id   INT NOT NULL REFERENCES doctors(id),
    date        DATE NOT NULL,
    available   BOOLEAN NOT NULL,             -- FALSE = on leave
    start_time  TIME,
    end_time    TIME,
    note        TEXT,
    UNIQUE (doctor_id, date, start_time)
);

-- Materialised bookable slots. Generated nightly for the next 21 days.
-- Materialised, not computed on the fly, so the slot row can act as the lock (§13.3).
CREATE TABLE slots (
    id          BIGSERIAL PRIMARY KEY,
    doctor_id   INT NOT NULL REFERENCES doctors(id),
    starts_at   TIMESTAMPTZ NOT NULL,
    ends_at     TIMESTAMPTZ NOT NULL,
    capacity    SMALLINT NOT NULL DEFAULT 1,
    UNIQUE (doctor_id, starts_at)
);
CREATE INDEX slots_lookup ON slots (doctor_id, starts_at)
  WHERE starts_at > now();

-- ============ Transactional data ============

CREATE TABLE patients (
    id            BIGSERIAL PRIMARY KEY,
    phone         TEXT NOT NULL,              -- E.164; encrypted at rest (§21.4)
    full_name     TEXT NOT NULL,
    age           SMALLINT,
    mrn           TEXT,                       -- hospital record no., if known
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (phone, full_name)
);

CREATE TYPE appt_status AS ENUM ('held','booked','cancelled','completed','no_show');

CREATE TABLE appointments (
    id              BIGSERIAL PRIMARY KEY,
    reference       TEXT NOT NULL UNIQUE,     -- "SPC-8341", read to the caller
    slot_id         BIGINT NOT NULL REFERENCES slots(id),
    doctor_id       INT NOT NULL REFERENCES doctors(id),
    patient_id      BIGINT REFERENCES patients(id),
    status          appt_status NOT NULL,
    reason_for_visit TEXT,                    -- verbatim, never interpreted (§13.4)
    source          TEXT NOT NULL,            -- 'ai_voice' | 'reception' | 'walk_in'
    call_id         TEXT,                     -- links to calls.session_id
    hold_token      TEXT UNIQUE,              -- doubles as the idempotency key
    hold_expires_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The double-booking guard (§13.3)
CREATE UNIQUE INDEX uniq_active_slot ON appointments (slot_id)
  WHERE status IN ('held','booked');

-- ============ Call data ============

CREATE TYPE call_disposition AS ENUM (
    'completed_booking', 'completed_faq', 'transferred_out_of_scope',
    'transferred_escape', 'transferred_emergency', 'transferred_ai_failure',
    'abandoned', 'abandoned_during_transfer', 'rejected_at_capacity', 'error'
);

CREATE TABLE calls (
    session_id      TEXT PRIMARY KEY,         -- Asterisk UNIQUEID (§17.2)
    did             TEXT NOT NULL,
    caller_number   TEXT NOT NULL,            -- encrypted at rest
    started_at      TIMESTAMPTZ NOT NULL,
    answered_at     TIMESTAMPTZ,
    ended_at        TIMESTAMPTZ,
    duration_s      INT,
    disposition     call_disposition,
    language        TEXT,
    final_state     TEXT,
    turn_count      INT,
    llm_call_count  INT,
    appointment_id  BIGINT REFERENCES appointments(id),
    transferred_to  TEXT,                     -- '122' | '123' | 'queue'
    error_code      TEXT
);
CREATE INDEX calls_started ON calls (started_at DESC);

CREATE TABLE call_turns (
    id            BIGSERIAL PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES calls(session_id) ON DELETE CASCADE,
    turn_no       SMALLINT NOT NULL,
    speaker       TEXT NOT NULL CHECK (speaker IN ('caller','ai')),
    text          TEXT NOT NULL,              -- PII-redacted before write (§21.5)
    state         TEXT,
    stt_ms        INT,
    llm_first_token_ms INT,
    tts_first_byte_ms  INT,
    e2e_ms        INT,
    barge_in      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE transfers (
    id            BIGSERIAL PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES calls(session_id),
    reason        TEXT NOT NULL,
    method        TEXT NOT NULL,              -- 'refer' | 'bridged'
    target        TEXT NOT NULL,
    succeeded     BOOLEAN NOT NULL,
    context_json  JSONB,                      -- what the screen-pop showed
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    actor         TEXT NOT NULL,              -- 'ai:{session_id}' | 'user:{id}'
    action        TEXT NOT NULL,              -- 'appointment.create', etc.
    entity_type   TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    before_json   JSONB,
    after_json    JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX audit_entity ON audit_log (entity_type, entity_id, created_at DESC);
```

**`slots` is materialised rather than computed on demand for one specific reason:** you
cannot take a row lock on a slot that does not exist as a row. Materialisation is what makes
§13.3's uniqueness constraint possible, and it makes availability queries a simple indexed
range scan instead of a recurrence-rule expansion on the latency-critical path.

---

# 19. API Design

Two distinct surfaces. Keep them separate — the LLM never sees the internal one.

## 19.1 Booking API (internal, consumed by the FSM)

Base: `http://127.0.0.1:8080/api/v1` — loopback or LAN only, never internet-exposed.

### `GET /doctors`
```
Query:  q (fuzzy name, searches full_name + aliases), department_id, active
Return: 200 [{ id, display_name, name_ta, department: {id,name},
               qualification, consult_minutes, match_score }]
Notes:  match_score lets the FSM decide between confirm / disambiguate / widen (§13.2).
        Trigram similarity on name + aliases, because ASR output is noisy.
```

### `GET /doctors/{id}/availability`
```
Query:  date_from (default today), date_to (default +7d, max +21d)
Return: 200 { doctor_id, days: [ { date, slots: [{ slot_id, starts_at, available }] } ] }
Notes:  Applies doctor_schedules minus schedule_exceptions minus taken slots.
        Read-only. No side effects. p95 target < 60 ms.
```

### `GET /slots`
```
Query:  doctor_id (required), date, limit (default 3), after (ISO timestamp)
Return: 200 { slots: [{ slot_id, starts_at, ends_at }] }
Notes:  The primary call in the OFFER_SLOTS state. Returns only genuinely open slots.
        The FSM records these slot_ids on the session; the LLM may offer NOTHING ELSE.
```

### `POST /slots/{slot_id}/hold`
```
Body:   { session_id, ttl_seconds: 120 }
Return: 201 { hold_token, expires_at }
        409  { error: "slot_taken" }        <- another caller won the race
Notes:  Creates an appointments row with status='held'. The unique partial index does
        the arbitration. This endpoint is the concurrency control for the whole system.
```

### `POST /slots/{slot_id}/release`
```
Body:   { hold_token }
Return: 204
Notes:  Idempotent. Called on transfer, hangup, and by the janitor.
```

### `POST /appointments`
```
Header: Idempotency-Key: {hold_token}          <- REQUIRED
Body:   { hold_token, patient: { full_name, age, phone },
          reason_for_visit?, session_id }
Return: 201 { reference: "SPC-8341", slot: {...}, doctor: {...} }
        409 { error: "hold_expired" }
        410 { error: "hold_not_found" }
Notes:  Promotes 'held' -> 'booked' in one transaction, upserts the patient, writes
        the audit_log row. Replaying the same Idempotency-Key returns the ORIGINAL 201
        response, never a duplicate booking. This is what makes the retry in §13.2 safe.
```

### `PATCH /appointments/{reference}` — Phase 2 (reschedule)
### `DELETE /appointments/{reference}` — Phase 2 (cancel)

MVP is **book-only**. Reschedule and cancel require identity verification that voice alone
cannot provide safely, and both are transferred to a human in MVP (§26).

### `GET /faqs`
```
Query:  q
Return: 200 [{ id, question, answer_en, answer_ta, category }]
Notes:  A curated table, not a vector store. Roughly 30-50 entries at MVP.
        Retrieval by keyword + trigram. If no entry scores above threshold,
        the FSM transfers. It does NOT let the LLM improvise. (§13.1 applies here too.)
```

### `POST /internal/screenpop`
```
Body:   { session_id, caller_number, reason, context: {...} }
Return: 202
```

## 19.2 The LLM tool surface

A deliberately narrow set. **Each tool is a thin, validated wrapper over one Booking API
call. The LLM cannot reach the database, and it cannot write anything.**

| Tool | Maps to | Write? |
|---|---|---|
| `find_doctor(name?, department?)` | `GET /doctors` | No |
| `get_available_slots(doctor_id, date_hint)` | `GET /slots` | No |
| `lookup_faq(question)` | `GET /faqs` | No |
| `request_transfer(reason)` | Signals the FSM | No — the FSM performs it |
| `set_collected_field(field, value)` | Writes to `CallSession` only | No |
| `confirm_booking()` | Signals the FSM | **No** — the FSM calls `POST /appointments` |

**`confirm_booking` deliberately does not perform the booking.** It sets a flag that the
FSM checks after verifying that the readback occurred and that the caller said yes. This
is the mechanical guarantee behind §13.1's third rule: the model requests, the state
machine decides.

---

# 20. Error Handling

**One governing rule:** *every* failure path terminates at either a working AI conversation
or a human being. **No failure path terminates at silence.**

## 20.1 The failure matrix

| Failure | Detection | Immediate behaviour | Caller hears | Fallback |
|---|---|---|---|---|
| **Sarvam STT fails** | WS close / no transcript in 3 s | Reconnect once, replay 2 s ring buffer | Nothing (invisible if fast) | 2nd failure -> transfer |
| **STT returns garbage twice** | Confidence + length heuristics | Escalate | "Enakku sariyaa kekkala, naan connect panren" (WAV) | Transfer |
| **Sarvam LLM 429** | HTTP 429 | Retry once, 300 ms jitter | Filler audio | Transfer. **Alert — plan tier signal** |
| **Sarvam LLM 5xx / timeout > 2.5 s** | Timeout | Filler, one retry | "Oru nimisham" (WAV) | Transfer |
| **Sarvam TTS fails** | WS close / no audio in 800 ms | Swap to warm spare socket | Nothing | Pre-recorded WAV -> transfer |
| **Internet drops** | 3 failures across services in 60 s | **Circuit breaker opens** | In-flight: transfer msg | New calls get SIP 503 -> COM1PBX failover -> 122/123 |
| **AI server crashes** | COM1PBX `qualify` fails within 30 s | — | Nothing | **PBX route failover** -> 122/123. `systemd Restart=always` brings it back. In-flight calls are lost — an accepted MVP limitation. |
| **COM1PBX unreachable from AI** | SIP OPTIONS failure | Cannot transfer | Callback message (WAV), graceful hangup | Alert CRITICAL |
| **Booking API down** | Connection refused / 5xx | Do not offer booking | "Booking system-la problem irukku, team-kitta connect panren" | Transfer |
| **Availability query times out (>250 ms)** | Hard timeout | Do not stall the turn | Filler, then one retry | Transfer on 2nd failure |
| **AI response too slow (>2.0 s)** | Turn timer | Filler audio at 700 ms | "mm / okay" (WAV) | Transfer at 3.5 s |
| **Caller silent** | VAD, no speech | 6 s -> reprompt; 12 s -> reprompt; 18 s -> "Call-a mudikkiren" then hang up | Reprompts (WAV) | Graceful hangup, disposition `abandoned` |
| **Caller interrupts (barge-in)** | VAD during TTS | Stop < 150 ms, cancel LLM, truncate history (§10.3) | Own voice, uninterrupted | Normal — **not an error** |
| **Caller speaks over TTS continuously** | Barge-in 3x in 3 turns | Likely echo or a talkative caller | Increase VAD threshold once; if it persists, transfer | Transfer |
| **Call drops unexpectedly** | Asterisk hangup handler | Full cleanup (§15.4), **release the held slot** | — | Disposition recorded |
| **Postgres down** | Connection failure | Circuit break booking; FAQs still work from cache | "Booking system problem" | Transfer for all booking intents. Alert CRITICAL |
| **Redis down** | Connection failure | **Degrade, do not fail.** Live registry and shared rate limiting are lost; calls continue | Nothing | Warn. Single-process MVP tolerates this. |
| **At capacity (25 calls)** | Admission control | SIP 503 before answering | Nothing — never answered by AI | COM1PBX failover -> 122/123 |

## 20.2 Pre-recorded audio inventory

These WAV files are synthesised once, reviewed by a native Tamil speaker, committed to the
repo, and **must never depend on Sarvam at runtime.** They are the reason an AI outage
sounds like a polite handoff instead of dead air.

| Key | Content (Tamil + English variants) |
|---|---|
| `greeting` | "Vanakkam, SPC Hospital. How can I help you? Tamil-la pesalaam." |
| `filler_1..3` | "mm", "okay", "oru nimisham" |
| `transfer_generic` | "Naan ungala hospital team-kitta connect panren." |
| `transfer_emergency` | "Udane connect panren, line-la irunga." |
| `cant_hear` | "Enakku sariyaa kekkala. Naan connect panren." |
| `system_problem` | "System-la konjam problem. Team-kitta connect panren." |
| `all_busy_callback` | "Ellarum busy-a irukkanga. Ungaloda number note pannitten, call panruvanga." |
| `reprompt_1/2` | "Hello? Kekkudha?" / "Ennoda kooda pesuringala?" |
| `goodbye` | "Nandri. Nalla irunga." |

---
# 21. Security

Scoped to this architecture. Generic enterprise controls are omitted.

## 21.1 The single largest risk in this design

**Patient audio containing health information leaves the hospital and goes to Sarvam's
API.** That is not avoidable if Sarvam is the AI provider — but it is the fact that drives
most decisions below, and it requires an explicit, documented, hospital-approved decision
before go-live (§29, Q-C1).

Mitigations that are actually available:

| Control | Action |
|---|---|
| Contractual | A written **Data Processing Agreement with Sarvam** covering: no training on hospital audio, data retention period, deletion on request, breach notification, and India data residency. **Get this in writing before Gate 2.** |
| Minimisation | Send only the audio needed for the conversation. Never send stored recordings for batch processing. |
| No PHI in prompts | The system prompt contains no patient data. Per-call data is minimal (name, age, doctor, slot) and never medical. |
| Reason-for-visit | Stored verbatim, never sent to any endpoint other than STT (where it unavoidably passes as speech). |
| Disclosure | The greeting states the caller is speaking to an automated assistant. Required under DPDP notice obligations and basic decency. |

## 21.2 Network and SIP security

**The AI box sits on the hospital LAN, in a dedicated VLAN with the PBX.** This single
decision removes an entire class of problems:

- **SIP and RTP never touch the public internet.** No SIP over WAN, therefore no SIP
  scanning, no toll-fraud registration attempts, no NAT traversal, no jitter from
  the internet path.
- **It also side-steps the Indian PSTN/VoIP interconnection question.** Carrying PSTN
  audio over the public internet to a cloud endpoint raises DoT/TRAI questions that an
  on-premises LAN deployment simply does not raise. **Confirm with the hospital's telecom
  advisor regardless (§29, Q-C2)** — but on-prem is the low-risk posture.

| Control | Configuration |
|---|---|
| SIP transport | UDP 5060 on the private VLAN. **TLS 5061 if COM1PBX supports it (Q-13)** — nice to have, not required on an isolated VLAN. |
| SIP peer ACL | Asterisk `permit=<PBX IP>/32`, `deny=0.0.0.0/0`. No SIP registration accepted from anywhere else. |
| Anonymous SIP | `allowguest=no`, `alwaysauthreject=yes` |
| Host firewall | Inbound: SIP + RTP from the PBX IP only; SSH from the admin subnet only. Everything else denied. |
| Outbound firewall | HTTPS to `api.sarvam.ai` only. **Egress allowlist, not allow-all.** A compromised AI box should not be able to exfiltrate freely. |
| RTP | Media stays on the VLAN; SRTP unnecessary on an isolated segment and adds CPU. Revisit if the box ever moves off-LAN. |
| Fail2ban | On SSH and on Asterisk security events |

## 21.3 Application authentication and authorisation

| Surface | Control |
|---|---|
| Booking API | Bound to `127.0.0.1` only. Not reachable from the network at all. |
| Admin dashboard | Separate service; session auth; **role-based**: `viewer` (metrics only), `receptionist` (screen-pop + today's appointments), `admin` (doctor schedules), `engineer` (logs, no patient data by default) |
| Sarvam API key | Environment variable injected by systemd from a root-owned `0600` file. Never in the repo, never in logs, never in an image layer. |
| Database | The application connects as a role with `SELECT/INSERT/UPDATE` on its tables and **no DDL, no DELETE on `audit_log`**. |
| Asterisk ARI/AMI | Bound to loopback; strong generated password; used only by the local app |

**Secrets management:** for a single on-prem box, systemd `EnvironmentFile` with `0600`
root-owned files plus documented rotation is proportionate. HashiCorp Vault at this scale
is an unjustified operational burden — revisit if a second site is added.

## 21.4 Encryption

| Data | At rest | In transit |
|---|---|---|
| `patients.phone`, `calls.caller_number` | **pgcrypto column encryption**, key in the systemd secret file | — |
| Postgres as a whole | LUKS full-disk encryption on the data volume | Loopback only |
| Sarvam traffic | — | TLS 1.2+, certificate validation **on** (never `verify=False`) |
| Backups | Encrypted with a separate key, stored off the box | — |
| Pre-recorded WAVs, doctor data | Not sensitive | — |

## 21.5 PII handling and logging discipline

**The default is: do not log it.**

| Field | Logging rule |
|---|---|
| Caller phone number | **Masked in every log line**: `+91XXXXXX4630`. Full value only in the encrypted DB column. |
| Patient name | **Never in application logs.** DB only. |
| Age | Never in logs |
| Transcripts | Stored in `call_turns` (a controlled table), **not** in application logs |
| Reason for visit | DB only. Never logged, never in metrics, never in an alert payload. |
| Session ID | Logged freely — it is the correlation key and carries no PII |
| Metric labels | **Session ID and phone number are forbidden as Prometheus labels** — both are unbounded cardinality and a PII leak into a system with weaker access control than the DB |

A structured-logging filter enforces this: fields named `phone`, `name`, `age`,
`reason_for_visit` are redacted at the log formatter, not at each call site. **Enforcement
at the formatter is the only version of this rule that survives contact with a deadline.**

## 21.6 Call recording policy

The hospital already records calls (the `docs/AUDIO FILES SPC` evidence). For the AI leg:

| Decision | Recommendation |
|---|---|
| Record the AI-handled leg? | **Yes for the first 4 weeks** (needed to tune ASR, evaluate quality, and debug), **then off by default**, with the transcript in `call_turns` as the durable record. |
| Consent | The greeting must include a recording disclosure while recording is on. |
| Storage | Encrypted volume on the AI box. **Never sent to Sarvam or any third party.** |
| Retention while on | **30 days**, then automatic deletion by a cron job that is monitored |
| Access | `admin` role only, with an `audit_log` entry for every playback |

## 21.7 Data retention

| Data | Retention | Basis |
|---|---|---|
| Call recordings (AI leg) | 30 days | Tuning only |
| `call_turns` transcripts | **90 days**, then PII-redacted and kept aggregate | Quality analysis |
| `calls` metadata (no content) | 2 years | Operational analytics |
| `appointments` | Per hospital medical-records policy — **hospital decides, not engineering** (§29) |
| `audit_log` | 3 years, append-only | Accountability |
| Sarvam-side retention | **Per the DPA — must be contractually bounded** (§21.1) |

## 21.8 Audit logging

Every one of these writes an `audit_log` row with actor, before, after, and timestamp:

- Appointment created / modified / cancelled (actor `ai:{session_id}` or `user:{id}`)
- Doctor schedule or exception changed
- A transfer executed, with its reason
- Any staff access to a recording or a transcript
- Configuration changes (prompt version, FAQ content, emergency phrase list)

**The emergency phrase list and the system prompt are versioned in git and their deployment
is audit-logged.** They are clinical-safety artefacts, not configuration.

---

# 22. Observability

## 22.1 Per-call log record

One structured JSON line per call, plus one per turn. **No PII (§21.5).**

```json
{
  "session_id": "call_1755248400.123",
  "did": "2529500",
  "caller_masked": "+91XXXXXX4630",
  "started_at": "2026-08-15T14:03:20+05:30",
  "duration_s": 168,
  "language": "ta-IN",
  "turns": 9,
  "llm_calls": 8,
  "final_state": "END",
  "disposition": "completed_booking",
  "appointment_ref": "SPC-8341",
  "transferred_to": null,
  "barge_ins": 3,
  "latency_p50_ms": 1120,
  "latency_p95_ms": 1480,
  "errors": []
}
```

## 22.2 Metrics

**Telephony**
| Metric | Type | Alert |
|---|---|---|
| `calls_offered_total{did}` | counter | — |
| `calls_active` | gauge | > 20 for 5 min (approaching the 25 ceiling) |
| `calls_rejected_total{reason}` | counter | any `at_capacity` |
| `call_duration_seconds` | histogram | p95 > 300 s |
| `transfers_total{reason,target,success}` | counter | success rate < 95% |
| `sip_peer_up{peer="com1pbx"}` | gauge | **0 for 60 s -> CRITICAL** |
| `pbx_failover_events_total` | counter | any occurrence -> page |

**AI**
| Metric | Type | Alert |
|---|---|---|
| `stt_latency_ms` | histogram | p95 > 400 |
| `llm_first_token_ms` | histogram | p95 > 900 |
| `llm_total_ms` | histogram | p95 > 1800 |
| `tts_first_byte_ms` | histogram | p95 > 400 |
| **`turn_e2e_ms`** | histogram | **p95 > 1500 -> warn; > 2000 -> page.** *The single most important number in the system.* |
| `sarvam_errors_total{service,code}` | counter | any 429 -> warn |
| `sarvam_llm_requests_per_min` | gauge | **> 80% of plan ceiling -> upgrade signal (§12.4)** |
| `circuit_breaker_state{service}` | gauge | open -> page |
| `barge_ins_total` | counter | rate > 2/call suggests echo (§10.3) |
| `no_transcript_total` | counter | rising -> ASR/audio problem |
| `slot_offer_violations_total` | counter | **any occurrence -> page.** The LLM offered a slot the API did not return (§13.1). Highest-severity correctness signal in the system. |

**Appointment**
`booking_attempts_total`, `bookings_succeeded_total`, `bookings_failed_total{reason}`,
`slot_hold_conflicts_total`, `slot_holds_leaked_total` (janitor cleanups — should be ~0).

**Infrastructure**
CPU, RAM, disk, network; `audiosocket_connections`, `sessions_active`,
`postgres_pool_in_use`, `redis_up`, `asterisk_channels_active`.

## 22.3 Stack

**Prometheus + Grafana + Loki, all on the same box, in Docker.** No hosted APM. At one node
and 25 calls this is proportionate; a hosted APM would cost more than the AI inference.

Three dashboards, no more:

1. **Live Ops** — active calls, current p95 turn latency, transfer rate, PBX peer status.
   *For the receptionist supervisor.*
2. **Quality** — daily bookings, containment rate, abandonment, dispositions by category,
   top transfer reasons. *For the hospital administrator (persona P7).*
3. **Engineering** — latency histograms per stage, Sarvam error rates and rate-limit
   headroom, resource utilisation. *For on-call.*

## 22.4 Alert routing

| Severity | Examples | Route |
|---|---|---|
| **PAGE** | SIP peer down, circuit breaker open, `turn_e2e_ms` p95 > 2 s, any `slot_offer_violation`, Postgres down | Phone + WhatsApp to on-call, immediately |
| **WARN** | Sarvam 429s, latency p95 > 1.5 s, transfer success < 95%, `calls_active` > 20 | Daily digest |
| **INFO** | Capacity rejections, leaked slot holds | Dashboard only |

## 22.5 Quality review — the manual loop that actually matters

Metrics cannot detect a polite, fluent, wrong answer. Therefore:

- **Every day for the first month, a human reviews 10 random transcripts** and scores each
  on: correct intent, correct doctor, correct slot, appropriate transfer decision, language
  handling, and whether the AI asserted anything it was not given.
- **Every transfer with reason `ai_failure` is reviewed within 24 hours.**
- **Every call ending in `error` is reviewed the same day.**

This is a staffing commitment, not a tooling one. **Name the owner before go-live (§29).**

---

# 23. Infrastructure

## 23.1 Recommended stack

| Layer | Choice | Why this and not the alternative |
|---|---|---|
| **Telephony** | **COM1PBX** (unchanged) | It works, it is paid for, staff know it, and it is the failover path (§26) |
| **SIP/media gateway** | **Asterisk 20 LTS**, single-purpose | Something must terminate SIP and RTP. Asterisk in a 40-line media-gateway role is the smallest correct answer (§9.3). |
| **Backend language** | **Python 3.12 + FastAPI** | **Chosen over Node.js because the entire relevant ecosystem is Python-first:** Sarvam's SDK and reference integration, Pipecat, Silero VAD (ONNX), `librosa`/`soundfile` for resampling, and the numeric libraries used for audio. Node's async model is equally capable for the I/O, but you would be reimplementing plugins that already exist and are maintained in Python. This is an ecosystem decision, not a language-quality one. |
| **Media pipeline** | **Pipecat** with a custom AudioSocket transport | Sarvam maintains first-party plugins; turn-taking and interruption handling are genuinely hard (§10.4). |
| **VAD** | **Silero VAD** (ONNX, local) | ~3% of a core per call, no network round trip. Do not use a cloud VAD on the latency path. |
| **Database** | **PostgreSQL 16** | **Chosen over MongoDB decisively.** The core operation is *"prevent two callers booking one slot,"* which needs transactions, row locks, and unique partial indexes. Postgres does this natively; MongoDB would require application-level locking to emulate it — the exact pattern that fails under concurrency. Booking data is also relational (doctors -> schedules -> slots -> appointments). There is no document-shaped data in this system. |
| **Session state** | **In-process memory**, Redis only for the live registry and shared rate limiting | The call is pinned to one process anyway (§17.1) |
| **Cache/coordination** | **Redis 7** | Small role, keeps a second process cheap later |
| **Deployment** | **Docker Compose** on one Ubuntu 22.04 LTS box | 5 containers, one host, one file. See §23.3. |
| **Process supervision** | systemd (Asterisk, host-level) + Docker restart policies | |
| **Monitoring** | Prometheus + Grafana + Loki, same box | §22.3 |
| **CI** | GitHub Actions -> build image -> push to a private registry -> `docker compose pull && up` | |

## 23.2 Do we need LiveKit? — **No, not for the MVP**

**What LiveKit would provide:** a managed/self-hosted SIP ingress service, a WebRTC SFU,
room-based session management, and the LiveKit Agents framework with built-in turn
detection and telephony transports.

**What problem it actually solves:** multi-participant real-time media (many people in one
room), WebRTC browser clients, and SIP ingress you do not want to operate yourself.

**Why COM1PBX + Asterisk + Sarvam already solves this problem:**

| Requirement | LiveKit | This design |
|---|---|---|
| SIP ingress | LiveKit SIP service | Asterisk (40 lines of dialplan) |
| One caller <-> one agent | A room with 2 participants — an SFU used as a point-to-point pipe | An AudioSocket TCP connection |
| Multi-party conferencing | Native | **Not required. There is no requirement for more than one caller per call.** |
| WebRTC browser clients | Native | **Not required. Callers use phones.** |
| Turn detection | LiveKit Agents | Pipecat + Silero |
| Operational footprint | LiveKit server + Redis + SIP service + agent workers (4 components, 1 more if the SFU is HA) | Asterisk + one Python app |

**LiveKit's core value — the SFU — is the one thing this system has no use for.** A hospital
receptionist call is strictly point-to-point. Deploying an SFU to carry two-party audio is
paying full complexity for an unused capability.

**Reconsider LiveKit if and only if:** the hospital wants live browser-based call monitoring
or barge-in by supervisors; a second site needs shared infrastructure; three-way calls
(patient + AI + doctor) become a requirement; or operating Asterisk proves to be a
sustained burden for the team. **None of these are MVP requirements.** Note that
`docs/PRD.md` chose LiveKit for a different site with different constraints — that is not a
contradiction to resolve, it is a different set of tradeoffs.

## 23.3 Do we need LangGraph? — **No**

Compare the two honestly:

| | Deterministic FSM (recommended) | LangGraph |
|---|---|---|
| Booking correctness | The state machine calls `POST /appointments`. The path is enumerable and testable. | The graph decides; correctness is emergent |
| Testability | Every transition is a unit test. Full coverage is achievable. | Requires LLM-in-the-loop evaluation |
| Latency | Zero framework overhead on the turn path | Graph execution overhead per turn, which matters at a 1.5 s budget |
| Debugging a bad call | Read `state` from the log; the path is obvious | Trace a graph execution |
| Handles genuinely open-ended tasks | Poorly | Well |
| Multi-agent orchestration | Not supported | Its actual purpose |
| Clinical-safety review | A clinician can read the state diagram in §8.4 | A clinician cannot review a graph |

**The appointment flow has eight states and one branch point.** That is a switch statement,
not a graph. LangGraph is built for open-ended, multi-agent, dynamically-routed workloads —
the opposite of what a booking receptionist should be. Its flexibility is precisely the
property you do not want in a system that must never invent an appointment.

**The last row is the decisive one.** A hospital must be able to have a clinician review and
sign off the conversation logic. A state diagram supports that. A graph whose routing is
decided at runtime by an LLM does not.

**The LLM's job here is narrow and correct: understand messy code-mixed speech, and phrase
the response naturally.** It is a natural-language interface to a deterministic workflow, not
the workflow itself.

## 23.4 Do we need OpenRouter? — **No**

OpenRouter provides multi-provider LLM routing, a unified API, and automatic failover
across providers.

| Claimed benefit | Reality here |
|---|---|
| Multi-provider access | Sarvam is chosen precisely *because* it is best-in-class for Tamil and code-mixed Indic speech. There is no second provider we want to route to. |
| Unified API | We integrate one provider. There is nothing to unify. |
| Failover across providers | **Sarvam is not just the LLM — it is also the STT and the TTS.** If Sarvam is down, an alternative LLM does not save the call; the caller's speech cannot be transcribed. The correct failover is to a human (§20), not to another model. |
| Cost arbitrage | Adds a hop, a vendor, and a point of failure to a system whose entire latency budget is 1.5 s |
| Added latency | An extra network hop on the most latency-sensitive path in the system |
| Added risk | Patient audio-derived text would traverse a third party with no DPA (§21.1) |

**The third row is the argument that settles it.** OpenRouter would insure against the
failure of one component of a three-component dependency. It would not make the system more
available; it would make it less simple and more exposed.

**Revisit only if** Sarvam's LLM quality proves inadequate for Tamil intent detection while
its STT and TTS remain the best choice — in which case swap in a second LLM directly, behind
the one interface that already exists. That is a day of work, not an architectural
dependency to carry from day one.

## 23.5 Deployment topology

```
 ┌───────────────────── Hospital LAN, voice VLAN ─────────────────────┐
 │                                                                     │
 │   ┌──────────┐         SIP/RTP          ┌───────────────────────┐  │
 │   │ COM1PBX  │◄────────────────────────►│  AI Voice Server      │  │
 │   │          │      (private VLAN)      │  Ubuntu 22.04 LTS     │  │
 │   │ 122/123  │                          │  8 vCPU / 16 GB /     │  │
 │   └──────────┘                          │  200 GB SSD           │  │
 │                                          │                       │  │
 │                                          │  systemd: asterisk    │  │
 │                                          │  docker compose:      │  │
 │                                          │   - voice-app (Python)│  │
 │                                          │   - booking-api       │  │
 │                                          │   - postgres          │  │
 │                                          │   - redis             │  │
 │                                          │   - prometheus+grafana│  │
 │                                          │     +loki             │  │
 │                                          └───────────┬───────────┘  │
 └──────────────────────────────────────────────────────┼──────────────┘
                                                        │ HTTPS only,
                                                        │ egress allowlist
                                                        ▼
                                                 api.sarvam.ai
```

**One box. Five containers. One systemd service. No orchestrator.**

**Kubernetes is explicitly rejected.** It solves multi-node scheduling, rolling deploys
across replicas, and service discovery at scale. This system has one node, five containers,
and a hard dependency on being physically adjacent to the PBX. Kubernetes would add an
entire operational discipline the hospital's IT team does not have, to manage five
containers that do not move.

**On-premises rather than cloud, deliberately:** it removes 100–200 ms of network latency
from a 1.5 s budget, removes the PSTN-audio-over-internet regulatory question (§21.2),
removes monthly compute cost, and keeps working during an internet outage right up to the
point where Sarvam is unreachable — at which moment the circuit breaker hands every call
back to the humans sitting in the same building.

## 23.6 High availability

**MVP: single node, no HA — and that is a defensible decision**, because the failure mode is
benign. If the AI box dies, COM1PBX's route failover sends every call to 122/123 within 30
seconds and the hospital operates exactly as it does today. The blast radius of total AI
failure is "we go back to how things were last month."

Add a warm standby only if measured AI containment exceeds ~40% of inbound calls, at which
point losing the AI is a real operational event rather than a return to baseline.

---

# 24. Capacity Planning

## 24.1 Call model assumptions

| Parameter | Value | Source |
|---|---|---|
| Average AI call duration | **3.0 min** (booking), 1.5 min (FAQ), 0.75 min (fast transfer) | Estimate — **replace with CDR data (Q-14)** |
| Blended average | **2.4 min** | Weighted by §7 persona mix |
| Turns per booking call | 8–10 | Flow in §8.1 |
| LLM calls per turn | 1 | One request per caller utterance |
| AI speech per call | ~70 s (~40% of a 3 min call) | TTS billing driver |
| Design concurrency ceiling | **25** | Requirement |
| **Observed peak concurrency** | **2–4 inbound** | §0.6 — one day of recordings |
| Design daily volume | 300 AI calls/day (planning figure) | ~6x observed, deliberate headroom |
| Observed daily inbound | ~50 | §0.6 |

**Read the gap between rows 6 and 7 carefully.** The 25-call ceiling is a software design
target that costs nothing to support. It is not a purchasing target.

## 24.2 Per-call resource consumption

| Resource | Per call | At 25 concurrent |
|---|---|---|
| CPU | 0.08–0.15 core | 2–4 cores |
| RAM | 40–60 MB | 1.0–1.5 GB |
| LAN (SIP/RTP, both directions) | ~170 kbps | ~4.3 Mbps |
| Internet — STT upstream (PCM16 @ 16 kHz) | ~256 kbps | ~6.4 Mbps |
| Internet — TTS downstream (mulaw @ 8 kHz, ~40% duty) | ~26 kbps | ~0.7 Mbps |
| Internet — LLM (text) | negligible | negligible |
| **Internet total** | **~280 kbps** | **~7.1 Mbps** |
| Postgres connections | borrowed per query | peak ~10 of a pool of 20 |
| Sarvam STT WS | 1 | 25 |
| Sarvam TTS WS | 2 (1 + warm spare) | 50 |
| Sarvam LLM | ~2.7 req/min | **~67 req/min** |

**Note the STT bandwidth line.** If the 16 kHz upsampling experiment (§9.4) shows no
accuracy benefit, sending native 8 kHz halves internet upstream to ~3.2 Mbps. Worth
measuring at Gate 1 — it is the largest single bandwidth item.

## 24.3 Server sizing

| Component | Spec | Justification |
|---|---|---|
| **AI Voice Server** | **8 vCPU, 16 GB RAM, 200 GB NVMe SSD** | 2.7x CPU headroom, 10x RAM headroom at 25 calls |
| Internet | **20 Mbps symmetric, low jitter, business SLA** | 2.8x headroom over 7.1 Mbps. **Jitter and packet loss matter more than raw bandwidth** — a 100 Mbps line with 80 ms jitter is worse than a stable 20 Mbps one. |
| Backup internet | 4G/5G failover router | Sarvam unreachable = no AI. Cheap insurance. |
| UPS | Covering the AI box and the PBX | Both, or neither is useful |
| Postgres | Same box, 4 GB shared buffers | Load is trivial: a few hundred queries/minute |
| Disk growth | ~5 GB/month (transcripts + metrics + 30 days of recordings) | 200 GB is years of headroom |

**A single 8 vCPU box is genuinely sufficient.** The temptation to over-provision should be
resisted: idle capacity costs money every month and hides inefficiency.

## 24.4 When horizontal scaling becomes necessary

Do not scale on intuition. Scale on these triggers:

| Trigger | Action |
|---|---|
| `calls_active` p95 > 20 for a week | Raise the software ceiling to 40, verify CPU headroom |
| CPU > 60% sustained during peak | Add a second Python process on a second AudioSocket port; round-robin from Asterisk |
| `turn_e2e_ms` p95 > 1.5 s **and** CPU is low | **The bottleneck is Sarvam, not the server.** Upgrade the plan tier. Do not add servers. |
| `sarvam_llm_requests_per_min` > 80% of ceiling | Upgrade Sarvam plan |
| Two hospital sites | Second box at the second site — **not** a shared cloud deployment. Keeps the LAN-locality property. |
| AI containment > 40% of all inbound | Add a warm standby node (§23.6) |

**The third row is the one people get wrong most often.** Latency problems in this
architecture are far more likely to be API-side than compute-side, and adding servers makes
API-side rate limiting *worse*, not better.

---

# 25. Cost Model

All figures in INR. Sarvam prices are the published list rates as of 2026-08.

## 25.1 Sarvam unit prices

| Service | Price |
|---|---|
| STT (basic) | ₹30 / hour of audio, billed per second |
| TTS Bulbul v2 | ₹15 / 10,000 characters |
| TTS Bulbul v3 (beta) | ₹30 / 10,000 characters |
| LLM Sarvam-105B | ₹29.28 / 1M input · ₹10.98 / 1M cached input · ₹73.20 / 1M output |

## 25.2 Cost of one 3-minute booking call

| Component | Calculation | Cost |
|---|---|---|
| STT | 3 min = 0.05 h x ₹30 | **₹1.50** |
| TTS (Bulbul v2) | ~900 chars of AI speech x ₹15/10k | **₹1.35** |
| LLM input | 8 turns x ~1,500 tok = 12,000 tok; assume 60% cached: 4,800 x ₹29.28/M + 7,200 x ₹10.98/M | **₹0.22** |
| LLM output | 8 turns x 80 tok = 640 tok x ₹73.20/M | **₹0.05** |
| **Total per booking call** | | **₹3.12** |

| Call type | Duration | Cost |
|---|---|---|
| Booking | 3.0 min | ₹3.12 |
| FAQ | 1.5 min | ₹1.45 |
| Fast transfer (out of scope) | 0.75 min | ₹0.60 |
| **Blended average** | 2.4 min | **≈ ₹2.40** |

With Bulbul v3 the TTS component doubles, taking the booking call to ~₹4.47 and the blend to
~₹3.30. **Use v2 for MVP; A/B v3 on voice quality and only adopt it if callers measurably
prefer it.**

## 25.3 Runtime (variable) cost

| Scenario | Calls/day | AI min/day | AI min/month | Sarvam cost/month |
|---|---|---|---|---|
| **Observed** (§0.6) | 50 | 120 | ~3,100 | **≈ ₹3,100** |
| **Planning** | 300 | 720 | ~18,700 | **≈ ₹18,000** |
| **Stress** (25 concurrent, sustained) | 1,000 | 2,400 | ~62,400 | **≈ ₹60,000** |

*(26 operating days/month.)*

**The observed-volume column is the honest one.** At current call volumes, the AI's variable
cost is roughly ₹3,000/month — less than one day of a receptionist's salary.

## 25.4 Idle (fixed) infrastructure cost

| Item | On-premises (recommended) | Cloud (ap-south-1), for comparison |
|---|---|---|
| Server | ₹1.6–2.0 lakh **one-time**; ~₹2,800/mo amortised over 5 years | ₹18,000–22,000/mo (8 vCPU/16 GB) |
| Postgres | ₹0 (same box) | ₹6,000–9,000/mo (managed) |
| Redis | ₹0 (same box) | ₹1,500–2,500/mo (managed) |
| Monitoring | ₹0 (same box) | ₹0–8,000/mo |
| Internet (20 Mbps business) | ₹2,500–4,000/mo | (bandwidth billed separately) |
| 4G failover | ₹500/mo | n/a |
| Electricity + UPS | ~₹800/mo | ₹0 |
| **Fixed total** | **≈ ₹6,600–8,300/mo** + ₹2 L capex | **≈ ₹26,000–42,000/mo** |

**On-premises is roughly 4x cheaper on operating cost and also faster, simpler, and
regulatorily cleaner (§23.5).** The capex pays back in under 8 months.

## 25.5 Telephony cost

| Item | Cost |
|---|---|
| Existing DIDs and trunk | ₹0 incremental |
| **Additional trunk channels, if Gate 0 shows a shortfall** | ₹300–800 per channel/month (typical Indian SIP trunk). 16 extra channels ≈ ₹5,000–13,000/mo. **This is the largest unknown in the whole cost model.** |
| COM1PBX concurrency licence uplift, if required | **Unknown — Q-9** |
| Inbound call charges | ₹0 (inbound is free on Indian PSTN) |

## 25.6 Total cost of ownership

| | Observed volume | Planning volume |
|---|---|---|
| Sarvam (runtime) | ₹3,100/mo | ₹18,000/mo |
| Infrastructure (idle) | ₹7,500/mo | ₹7,500/mo |
| Telephony (incremental) | ₹0 – ₹13,000/mo | ₹0 – ₹13,000/mo |
| **Monthly total** | **₹10,600 – ₹23,600** | **₹25,500 – ₹38,500** |
| Capex, one-time | ₹2,00,000 | ₹2,00,000 |

**For context:** one full-time receptionist in Salem costs roughly ₹18,000–25,000/month
fully loaded. At planning volume the AI absorbs work that would otherwise need 2–3
additional staff at peak, for the cost of roughly one.

**The dominant cost is fixed, not variable.** The system costs nearly the same at 50
calls/day as at 300. This argues for routing *more* traffic to the AI once quality is
proven — the marginal call costs ₹2.40.

---

# 26. MVP Scope

## 26.1 In scope

| # | Capability | Acceptance criterion |
|---|---|---|
| 1 | Inbound call handling on both DIDs via the COM1PBX AI trunk | Call answered within 2 s, 100 of 100 test calls |
| 2 | Pre-recorded bilingual greeting | Plays in < 300 ms, always, including when Sarvam is down |
| 3 | Tamil / English / Tanglish handling | `codemix` mode; 100-call qualitative review passes |
| 4 | Intent detection over the defined scope set | >= 90% correct on a 100-call labelled test set |
| 5 | Doctor lookup by name or department, with disambiguation | Handles ASR name variants via the alias table |
| 6 | Doctor availability and schedule queries | Answers only from the API (§13.1) |
| 7 | Appointment slot lookup and offer (max 3) | **Zero `slot_offer_violations`** |
| 8 | Appointment booking with 120 s slot hold | Zero double-bookings under a 25-concurrent load test |
| 9 | Verbal confirmation with a spoken reference number | Read back twice, digit by digit |
| 10 | Curated FAQ set (30–50 entries) | No answer outside the table; unmatched -> transfer |
| 11 | Human transfer to 122/123 via SIP REFER | > 95% success; < 4 s from decision to ringing |
| 12 | `0` / "receptionist" escape hatch, every state | 100% success across all states |
| 13 | Emergency phrase detection -> priority warm transfer | 100% recall on the clinical phrase list |
| 14 | PBX-level failover to 122/123 when the AI is down | Verified by pulling the power on the AI box |
| 15 | Admission control at 25 calls -> SIP 503 | Verified by load test |
| 16 | Barge-in | Stop < 150 ms from speech onset |
| 17 | Per-call structured logging, PII-redacted | Manual audit of 200 log lines: zero PII |
| 18 | Prometheus metrics + 3 Grafana dashboards | §22.2 |
| 19 | Screen-pop for transferred calls | Receptionist confirms usability |
| 20 | 25-concurrent load test | p95 `turn_e2e_ms` <= 1.5 s |

## 26.2 Explicitly NOT in the MVP

| Excluded | Why | When |
|---|---|---|
| **Appointment cancellation** | Voice-only identity verification is not safe enough for a destructive operation. Transfer to a human. | Phase 2, with OTP |
| **Appointment rescheduling** | Same. | Phase 2 |
| WhatsApp / SMS confirmation | Needs a BSP, DLT template registration, consent capture | Phase 2 |
| Outbound calls of any kind | Different product, different consent regime | Phase 2+ |
| Department routing beyond 122/123 | The full extension map is not yet documented (§3.3) | Phase 2 |
| Lab / diagnostic / pharmacy booking | Different workflow and inventory | Phase 2 |
| Billing, insurance, reports queries | Transfer to human | Phase 2 |
| Symptom intake or triage | Clinical liability (§6) | Not planned |
| Patient identity lookup by phone / MRN | Needs HIS integration and a consent model | Phase 2 |
| EMR / HIS / ABDM integration | No integration specified | Phase 2+ |
| Payment collection | Prohibited | Never |
| Multi-site deployment | One site first | Later |
| High availability | Failover to humans is sufficient (§23.6) | On the §24.4 trigger |
| Voice biometrics / caller authentication | Disproportionate for MVP scope | — |
| Recording playback UI | Files on disk are enough for the tuning period | Phase 2 |

## 26.3 Delivery gates

| Gate | Content | Exit criterion |
|---|---|---|
| **Gate 0 — Telephony truth** | COM1PBX Q1–14 answered in writing; CDR analysed; trunk channel count confirmed with the telco | **Trunk capacity >= target concurrency, and external SIP peering confirmed supported.** Nothing else starts until this passes. |
| **Gate 1 — Audio loop** | Asterisk trunk up; one call end-to-end; STT/TTS quality measured on real PSTN audio in Tamil, English, and Tanglish; 8 kHz vs 16 kHz decided; barge-in validated over PSTN; REFER vs bridged transfer decided | One human holds a 2-minute conversation with p95 latency <= 1.5 s |
| **Gate 2 — Booking correctness** | FSM, Booking API, holds, idempotency; DPA with Sarvam signed | 200 automated booking scenarios, zero double-bookings, zero slot violations |
| **Gate 3 — Failure behaviour** | Every row of §20.1 tested, including pulling the power on the AI box mid-call | No test ends in caller silence |
| **Gate 4 — Pilot** | Behind AA option "1", ~20 calls/day, daily transcript review | 2 weeks; containment >= 30%; zero safety incidents |
| **Gate 5 — Ramp** | One DID direct, then both | Metrics in §5 sustained for 2 weeks |

---

# 27. Phase 2

Ordered by value-per-unit-effort, not by novelty.

| Priority | Capability | Prerequisite | Note |
|---|---|---|---|
| **1** | **WhatsApp appointment confirmation** | WhatsApp BSP + DLT templates | Highest value for the effort. Removes the "did I hear the reference number right?" failure, which is the weakest link in a voice-only booking. |
| **2** | **SMS confirmation** | DLT template registration | Fallback for non-WhatsApp users |
| **3** | **Appointment cancellation** | OTP verification | Reduces no-shows; the most-requested missing feature |
| **4** | **Appointment rescheduling** | Cancellation + OTP | Naturally follows |
| **5** | **Multi-department routing** | Full extension map + department ownership | Direct transfer to Pharmacy, Lab, Wards instead of via 122/123 |
| **6** | **Patient lookup by caller ID** | HIS integration + consent model | "Welcome back, Selvi Ramya" — large UX gain, real privacy design work |
| **7** | **Appointment reminder calls** | Outbound calling + DoT/DND compliance | Directly attacks no-show rate |
| **8** | **Lab / diagnostic booking** | Lab schedule data | Same FSM, different slot source |
| **9** | **Call quality evaluation** | LLM-as-judge over transcripts | Automates §22.5's manual review |
| **10** | **Analytics dashboard for administration** | 3 months of data | Peak-hour staffing, demand by doctor, abandonment trends |
| **11** | **AI-assisted human receptionist** | Screen-pop maturity | Live transcript + suggested answers for 122/123 |
| **12** | **Billing / insurance FAQ** | Curated content + clear escalation | Careful scoping — high error cost |
| **13** | **Follow-up / feedback calls** | Outbound + consent | |
| **14** | **Prescription / referral routing** | Clinical workflow definition | Requires clinical sign-off |

**Deliberately deferred indefinitely:** symptom triage, clinical advice, payment collection,
and any capability where a wrong answer causes clinical or financial harm.

---

# 28. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **R1** | **Trunk channels are far fewer than 25**, making the concurrency target unreachable | **High** | **High** | Gate 0. Budget for extra channels (§25.5). Re-scope to actual capacity — which §0.6 suggests is entirely adequate anyway. |
| **R2** | COM1PBX does not support external SIP peers, or does not honour REFER | Medium | **High** | Q-7, Q-11. Fallbacks: N extension registrations behind a ring group; bridged transfer. If *neither* works, the project needs an SBC in front of the PBX — a material re-scope. |
| **R3** | COM1PBX has no route-level failover, so an AI outage strands callers | Medium | **High** | Q-10. Substitute: `qualify` + monitored manual switch + a documented runbook. **Do not go live without one.** |
| **R4** | ASR accuracy on Tanglish over 8 kHz telephony audio is worse than demos suggest | **High** | **High** | Gate 1 measures it on *real PSTN audio*, not laptop mics. Mitigations: alias tables for doctor names, readback on every field, aggressive transfer thresholds. **If accuracy is unusable, this is a stop-the-project finding — surface it at Gate 1, not at Gate 4.** |
| **R5** | Latency exceeds 2 s and callers hang up | Medium | High | Streaming pipeline, pre-recorded audio, filler audio, capped output tokens, on-prem deployment. Monitored as the primary metric. |
| **R6** | Sarvam rate limits bite at peak (§12.4) | Medium | Medium | Plan tier sized from measured load; automatic upgrade signal in §22.2; per-call transfer on 429 |
| **R7** | Sarvam outage | Low–Medium | Medium | Circuit breaker -> SIP 503 -> PBX failover to humans. Blast radius is "back to baseline." |
| **R8** | **Echo on the PSTN leg triggers false barge-ins**, making the AI stutter | Medium | Medium | Asterisk echo cancellation; 200 ms sustained-speech requirement; **must be tested over real PSTN, not softphones** |
| **R9** | The AI states something clinically wrong or invents availability | Low | **Very High** | §13.1's three mechanical guards; `slot_offer_violations` paged; no clinical content in scope; daily transcript review |
| **R10** | Emergency call handled by the AI instead of a human | Low | **Very High** | Deterministic phrase list evaluated *before* the LLM; biased toward false positives; clinical sign-off on the list; 100% recall is an MVP acceptance criterion |
| **R11** | Patient data exposure via Sarvam | Low | High | DPA (§21.1), no PHI in prompts, minimal data, egress allowlist |
| **R12** | Receptionists resist or work around the system | Medium | Medium | Screen-pop that saves them time; involve 122/123 staff during Gate 1; pilot behind an opt-in AA option; never remove their ability to take calls |
| **R13** | Regulatory question on PSTN-to-internet audio | Low | Medium | On-prem LAN deployment; telecom advisor sign-off (Q-C2) |
| **R14** | Nobody owns the daily quality review after week 4 | **High** | Medium | Name the owner before go-live (§29). **This is the most commonly under-planned risk in voice AI projects and it degrades quality silently.** |
| **R15** | Slot holds leak, blocking real appointments | Low | Medium | DB-side `expires_at`, janitor sweep, `slot_holds_leaked_total` metric |
| **R16** | Scope creep into triage or clinical advice | Medium | **Very High** | §6 non-goals are contractual, not aspirational. Any change requires clinical sign-off. |

---

# 29. Open Questions

## 29.1 Blocking — Gate 0 cannot pass without these

| ID | Question | Owner |
|---|---|---|
| **Q-1** | **How many concurrent inbound calls can the hospital's telco trunk actually carry?** The hard ceiling on everything. | Engineering + telco |
| **Q-2** | COM1PBX concurrent-call licence limit and current tier | COM1PBX |
| **Q-7** | Is external SIP peering supported, and does it need a licence? | COM1PBX |
| **Q-10** | Does the inbound route support automatic failover to a fallback extension? | COM1PBX |
| **Q-11** | Does COM1PBX honour SIP REFER from an external peer? | COM1PBX |
| **Q-14** | 30-day CDR export for both DIDs | COM1PBX |

*(Full question text in §0.5.)*

## 29.2 Compliance — must be resolved before Gate 2

| ID | Question | Owner |
|---|---|---|
| **Q-C1** | **Is the hospital's compliance owner comfortable that patient audio is processed by Sarvam?** If not, the entire AI-provider choice reopens. | Hospital compliance |
| **Q-C2** | Does the hospital's telecom advisor confirm that on-prem LAN PSTN-to-AI audio raises no DoT/TRAI concern? | Hospital + telecom advisor |
| **Q-C3** | Is a DPA with Sarvam obtainable, covering no-training, retention, deletion, and India residency? | Legal + Sarvam |
| **Q-C4** | Is recording the AI leg required, or is the `call_turns` transcript sufficient for DPDP and clinical purposes? | Compliance + clinical |
| **Q-C5** | What is the retention policy for appointment records? | Hospital medical records |

## 29.3 Operational — needed before Gate 4

| ID | Question | Owner |
|---|---|---|
| **Q-O1** | **What actually happens today when 122 and 123 are both busy** — queue, voicemail, or fast busy? If fast busy, a queue must be configured before go-live (§14.3). | Hospital operations |
| **Q-O2** | **Who owns the daily transcript review (§22.5) after week 4?** Unowned quality review is R14. | Hospital |
| **Q-O3** | Who signs off the emergency phrase list clinically, and by when? | Clinical lead |
| **Q-O4** | Full extension map and department ownership — the recordings show 111, 244, 600, 616, 666, Dr Bala, Pharmacy, OT Theatre, Female Ward | Hospital operations |
| **Q-O5** | Can the screen-pop page coexist with the receptionists' existing client console? | Hospital IT |
| **Q-O6** | Which doctors are in scope for MVP booking, and who maintains their schedules? | Hospital operations |
| **Q-O7** | Is there an existing appointment system that must be the source of truth? If yes, §18's schema becomes an integration layer instead of a system of record — **a material change to §13 and §19.** | Hospital |
| **Q-O8** | The fallback destination for an emergency when both 122 and 123 fail to answer | Clinical + operations |

## 29.4 Technical — resolve during Gate 1

| ID | Question |
|---|---|
| **Q-T1** | 8 kHz native vs 16 kHz upsampled to Sarvam STT — measure accuracy and bandwidth, decide empirically (§9.4) |
| **Q-T2** | Pipecat + a custom AudioSocket transport, or a hand-rolled pipeline? (§10.4) |
| **Q-T3** | Bulbul v2 vs v3 — 2x the cost; is the voice quality difference audible over 8 kHz telephony? |
| **Q-T4** | Actual measured VAD endpoint timing for Tamil speech — is 400 ms right? Tamil sentence-final patterns may need tuning. |
| **Q-T5** | `saaras:v3` vs `saaras:v3-realtime` — is mid-call language reconfiguration worth the migration? |
| **Q-T6** | Does Sarvam's WebSocket connection-rate limiter trip under barge-in-heavy load? (§12.4) |

---

# 30. Final Recommended Architecture

```
                                  PATIENTS
                                     │
                     ┌───────────────┴───────────────┐
                     │                               │
              0427-2529500                     0427-2312020
                     │                               │
                     └───────────────┬───────────────┘
                                     │
                          ╔══════════▼══════════╗
                          ║  TELCO TRUNK        ║  <-- HARD CEILING (Gate 0)
                          ║  N channels         ║      N is UNKNOWN
                          ╚══════════╤══════════╝
                                     │
                          ┌──────────▼──────────┐
                          │      COM1PBX        │   UNCHANGED, still the
                          │  (telephony system  │   telephony system of record
                          │   of record)        │
                          └──────────┬──────────┘
                                     │
                          ┌──────────▼──────────┐
                          │  Auto Attendant     │   existing AA_New_IN-SLM
                          │  "1 for appointments"│
                          └──────────┬──────────┘
                                     │
                   ┌─────────────────┴──────────────────┐
                   │                                    │
          SIP trunk "AI-GATEWAY"              route failover on
          static IP, 25 channels              unreachable / 5xx / 4s
                   │                                    │
                   │                                    ▼
                   │                          ┌──────────────────┐
                   │                          │ Ring group       │
                   │                          │  [122, 123]      │──► HUMAN
                   │                          └──────────────────┘
                   │                                    ▲
    ═══════════════▼════════════════════════════════════│═══════════════════
    ║          AI VOICE SERVER — on hospital LAN         │                  ║
    ║          8 vCPU / 16 GB / Ubuntu 22.04             │                  ║
    ║                                                    │                  ║
    ║   ┌────────────────────────────────────────┐       │ SIP REFER        ║
    ║   │  ASTERISK  (SIP + RTP media gateway)   │───────┘                  ║
    ║   │  ~40 lines of dialplan. Not a PBX.     │                          ║
    ║   └───────────────────┬────────────────────┘                          ║
    ║                       │ AudioSocket TCP, 8 kHz PCM16, 1 per call      ║
    ║   ┌───────────────────▼────────────────────────────────────────────┐  ║
    ║   │  PYTHON APP  (FastAPI + Pipecat, asyncio, one process)         │  ║
    ║   │                                                                 │  ║
    ║   │   CallSession x N   ── total isolation, nothing module-level    │  ║
    ║   │        │                                                        │  ║
    ║   │   ┌────▼─────┐   ┌──────────────┐   ┌────────────────────────┐ │  ║
    ║   │   │ Silero   │   │ DETERMINISTIC│   │  Admission control     │ │  ║
    ║   │   │ VAD      │──►│ FSM (§8.4)   │   │  + circuit breaker     │ │  ║
    ║   │   │ barge-in │   │ owns ALL     │   │  -> SIP 503 -> PBX     │ │  ║
    ║   │   └──────────┘   │ writes       │   │     failover           │ │  ║
    ║   │                  └──┬────┬───┬──┘   └────────────────────────┘ │  ║
    ║   └─────────────────────│────│───│───────────────────────────────┘  ║
    ║                         │    │   │                                   ║
    ║        ┌────────────────┘    │   └──────────────┐                    ║
    ║        │                     │                  │                    ║
    ║   ┌────▼──────┐    ┌─────────▼────────┐   ┌─────▼──────┐             ║
    ║   │ BOOKING   │    │  Pre-recorded    │   │ Prometheus │             ║
    ║   │ API       │    │  WAV bank        │   │ + Grafana  │             ║
    ║   │ (loopback)│    │  (works offline) │   │ + Loki     │             ║
    ║   └────┬──────┘    └──────────────────┘   └────────────┘             ║
    ║        │                                                             ║
    ║   ┌────▼───────┐  ┌────────┐                                         ║
    ║   │ PostgreSQL │  │ Redis  │  live registry + rate limiting only     ║
    ║   │ system of  │  └────────┘                                         ║
    ║   │ record;    │                                                     ║
    ║   │ slot lock  │                                                     ║
    ║   └────────────┘                                                     ║
    ═══════════════════════════════╤═══════════════════════════════════════
                                   │ HTTPS, egress allowlist
                                   ▼
                    ┌──────────────────────────────┐
                    │        SARVAM  API           │
                    │                              │
                    │  saaras:v3   (STT, codemix)  │
                    │  sarvam-105b (LLM)           │
                    │  bulbul:v2   (TTS, mulaw 8k) │
                    └──────────────────────────────┘
```

## 30.1 The five decisions that define this architecture

1. **The AI is a SIP trunk, not an extension.** This is what makes concurrent independent
   AI calls possible at all (§0.1). Everything else in the document is downstream of it.
2. **The failover lives in the PBX, not in the AI.** COM1PBX routes to 122/123 when the AI
   is unreachable. The AI cannot fail in a way that strands a caller, because the component
   that decides where calls go is not the component that can crash (§11.1).
3. **The state machine owns every write; the LLM only understands and phrases.** No
   appointment exists that the FSM did not create from an API-returned slot (§13.1).
4. **Pre-recorded audio for every fixed utterance.** The greeting, the transfer message, and
   every error message work when Sarvam does not (§10.2, §20.2).
5. **On-premises, one box, no orchestrator.** Removes latency, cost, and a regulatory
   question in one decision (§23.5).

## 30.2 Architecture philosophy, restated as commitments

| Principle | How this design honours it |
|---|---|
| Simple first | One box, five containers, one Python process, 40 lines of dialplan |
| Production-grade | Circuit breakers, admission control, idempotency, DB-enforced booking constraints, audit logs |
| No unnecessary frameworks | Pipecat for audio plumbing only — the FSM is plain Python. No LangGraph, no agent framework. |
| No unnecessary agents | One LLM, one role: understand speech and phrase replies |
| No unnecessary microservices | Two processes: media gateway and application |
| Deterministic booking | FSM + Postgres constraints + idempotency keys |
| Sarvam as primary AI provider | All three stages, one vendor, one key |
| COM1PBX remains the telephony system | One trunk and one route branch added. Nothing removed. |
| Human escalation always exists | Six independent paths to a human (§8.3), one of which requires no working software at all |
| AI failure never strands a caller | PBX-level failover + circuit breaker + pre-recorded audio + SIP 503 admission control |
| Every call gets an isolated session | Per-call SIP dialog, RTP stream, AudioSocket connection, `CallSession`, and Sarvam sockets |
| Designed for 25 concurrent calls | Software ceiling 25, provisioned for 8, 2.7x CPU headroom (§24) |
| Scale only when metrics justify it | Explicit numeric triggers in §24.4 |
| Boring infrastructure over fashionable | Asterisk, Postgres, Redis, Docker Compose, systemd |

## 30.3 What to do first

**Do not write integration code yet.** In order:

1. **Send the §0.5 questions to COM1PBX today.** Everything depends on the answers, and
   vendor response time is the longest pole.
2. **Get the CDR (Q-14) and the trunk channel count from the telco (Q-1).** These two
   numbers determine whether the 25-call target is achievable and whether it is necessary.
3. **In parallel, run the Gate 1 audio experiment** — a laptop, the Sarvam API, and 30
   recordings from `docs/AUDIO FILES SPC`. Measure Tanglish ASR accuracy on real 8 kHz
   telephony audio. **This is the cheapest way to find R4, which is the highest-likelihood
   project-killing risk, and it needs no PBX access at all.**
4. **Start Q-C1 and Q-C3** (compliance approval, Sarvam DPA) — both have long lead times.

Everything else waits on these four.

---

*End of document.*
