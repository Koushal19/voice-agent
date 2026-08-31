# Hospital Voice Agent — Product Requirements Document

**Status:** Draft for review — Gate 0 unresolved (§14)
**Deployment:** Single hospital, Chennai. Inbound calls on the hospital's existing SIP PBX.
**Languages:** Tamil, Hindi, English, and code-mixed (Tanglish) speech.

---

## 1. Problem

Inbound calls to the hospital's main number currently ring extensions 122 and 123, staffed
by humans using an existing client console that handles call-taking and recording. Volume
exceeds what two extensions can absorb; callers queue or abandon. A large share of those
calls are routine — appointment booking, timings, department and location questions — that
do not require a person.

## 2. Goals

1. Answer every inbound call immediately, in the caller's language.
2. Resolve routine requests end to end without a human: appointment booking, FAQs,
   structured symptom intake.
3. Transfer to a human at 122/123 whenever the caller asks, the agent is out of its depth,
   or a **potential emergency is detected** — with emergency transfers taking priority over
   everything else.
4. Never degrade below the current experience. Any failure path ends at a human, never at
   silence or a dropped call.

## 3. Non-goals

| Out of scope | Reason |
|---|---|
| Medical diagnosis or advice | Clinical liability. Triage is intake-only (§7.3). |
| Outbound calling campaigns | Different product. |
| ABDM / ABHA record read-write | Separate integration with its own consent architecture. The `abha_id` column exists; nothing is wired to it. |
| EMR write-back | No EMR integration specified. |
| A second cloud-hosted phone number | No number exists. Revisit if DR or overflow capacity is needed. |
| Replacing the existing client console | Extensions 122/123 and their recording behaviour are untouched. |
| Multi-instance horizontal scaling | §11.2. |

## 4. Success metrics

| Metric | Target |
|---|---|
| Turn latency (end-of-speech → first audio byte), p95 | ≤ 1.5s |
| Turn latency, hard ceiling | 2.0s — breach triggers the degradation path (§10) |
| Barge-in stop time | ≤ 150ms from caller speech onset |
| Emergency phrase → transfer initiated | ≤ 1 round-trip, 100% of the clinical phrase list |
| Calls resolved without human transfer | ≥ 50% of routine calls (baseline set after 2 weeks live) |
| Dropped / silent calls | 0 |
| ASR accuracy on code-mixed speech | Threshold set at Gate 0 from measured baseline (§14, S3) |

---

## 5. Architecture

### 5.1 Primary design — LiveKit SIP

The PBX points a **SIP trunk** at a self-hosted LiveKit deployment. Calls arrive as
participants in a LiveKit room; a Python agent worker joins that room and runs the
conversation. SIP is platform-agnostic, so this design does not depend on the PBX being any
particular product.

```
PSTN caller
     │
     ▼
┌──────────────────┐   SIP trunk (static IP peer)   ┌─────────────────────────────┐
│  Hospital SIP    │ ─────────────────────────────► │  LiveKit SIP service        │
│  PBX             │                                 │  LiveKit server (SFU)       │
│                  │ ◄───────────────────────────── │  Redis (coordination)       │
│  ext. 122 / 123  │        SIP REFER (transfer)     └──────────────┬──────────────┘
│  existing client │                                                │ WebRTC room
│  console + rec.  │                                                ▼
│  — unchanged     │                                 ┌─────────────────────────────┐
└──────────────────┘                                 │  Agent worker (Python)      │
                                                     │   Sarvam STT  (saarika)     │
                                                     │   Safety classifier ─┐      │
                                                     │   LLM via OpenRouter │      │
                                                     │   Sarvam TTS  (bulbul)│     │
                                                     └───────────┬───────────┴─────┘
                                                                 │
   ┌─────────────────────┐   ┌──────────────────┐                │
   │ FastAPI REST /api/v1│   │ arq worker       │ ◄──────────────┘
   │ patients, appts,    │   │ notify_emergency │
   │ calls, admin        │   │ summarize_call   │
   └──────────┬──────────┘   └────────┬─────────┘
              │                       │
       ┌──────▼───────┐        ┌──────▼──────┐
       │  Postgres    │        │   Redis     │
       │  domain data │        │  arq broker │
       └──────────────┘        └─────────────┘
```

**Three processes:** the LiveKit agent worker (voice), the FastAPI app (REST + admin), and
the arq worker (background jobs). They share Postgres and Redis and are deployed together.

### 5.2 Why this shape

- **Barge-in, VAD, turn detection, and playout truncation are the framework's job.** These
  are the failure modes that kill voice agents in production — a caller interrupts and keeps
  getting talked over because audio is already buffered downstream. LiveKit Agents handles
  interruption and truncates both playout and the agent's own transcript to what the caller
  actually heard.
- **Sarvam support is first-party** — `livekit-plugins-sarvam` provides STT (`saarika`) and
  TTS (`bulbul`), with a published production tuning guide. Confirm exact model versions at
  build time; they change.
- **SIP is universal.** Every PBX can route a trunk to `ip:5060`, so the integration does not
  break if the PBX turns out to be something other than expected.
- **Transfer is first-class.** Cold transfer via SIP REFER and warm (agent-assisted) transfer
  are both supported. v1 uses cold transfer; warm is available without redesign if the
  hospital asks for it.

### 5.3 Networking and data residency

LiveKit is **self-hosted on hospital-controlled infrastructure**. Patient voice audio must
not traverse a third-party media SFU (§9). This costs three services in the hot path
(LiveKit server, SIP service, Redis) and requires:

- SIP signaling on `5060`, RTP media on `10000–20000/udp`.
- LiveKit's documentation assumes these are Internet-reachable. Where PBX and LiveKit share
  a LAN, private-only operation with `use_external_ip: false` is the intended configuration
  and **must be verified at Gate 0** — it is off the documented happy path.
- LiveKit does **not** support SIP REGISTER. The PBX must be configured with a static,
  IP-authenticated trunk peer, not a registration.
- The PBX must accept and act on inbound **SIP REFER** for the 122/123 transfer to work.

### 5.4 Latency budget

Measured end-of-speech → first audio byte, target p95 ≤ 1.5s:

| Stage | Budget |
|---|---|
| Sarvam STT finalize | 150–300ms |
| Safety classifier (deterministic, in-process) | < 5ms |
| OpenRouter TTFT | 200–500ms |
| Sarvam TTS TTFB | 150–300ms |
| SIP / WebRTC / LAN transport | measured at Gate 0 |

This holds **only** if every stage streams: LLM tokens are buffered to sentence boundaries
and pushed to TTS as they form, never waiting for a complete response. One LLM round-trip
per turn (§7.1) is a hard design constraint — a second sequential model call does not fit.

### 5.5 Contingency — ARI + AudioSocket

If Gate 0 rejects LiveKit (compliance blocks self-hosting, LAN-only networking proves
unworkable, or SIP REFER cannot be made to work), the fallback is a direct Asterisk
integration. It requires Asterisk 18+ with `res_audiosocket` and ARI enabled. Requirements
that are easy to get wrong and are therefore stated as requirements, not implementation
detail:

- **Keep the channel in Stasis.** Create the media leg with
  `POST /channels/externalMedia` (`format=slin`, `encapsulation=audiosocket`,
  `transport=tcp`, `data=<call_uuid>`, `direction=both`) and bridge it to the caller
  channel. Do **not** use `POST /channels/{id}/continue` into an `AudioSocket()` dialplan
  hop — that removes the channel from the app and breaks the transfer. Supplying the UUID in
  the request also makes it the single source of truth for correlating control and media
  legs.
- **Audio output must be paced.** Write 320-byte frames on a 20ms monotonic cadence through
  a bounded queue (~5 frames). Writing as fast as the socket drains buffers seconds of
  speech downstream, and cancelling generation does not unplay it — barge-in silently fails.
  On interrupt, drain the queue; keep the writer task alive.
- Format is 8kHz 16-bit signed PCM, raw, in both directions.
- The whole voice path becomes our code, including VAD handling, interruption, and reconnect
  logic. Budget accordingly — this branch is materially more work than the primary design.

---

## 6. Call flow

1. **Answer.** Every inbound call reaches the agent first.
2. **Disclosure** (~4s, recorded audio, not synthesized): automated assistant, call is
   recorded, say "operator" at any time. Barge-in is live throughout — the caller's first
   word pre-empts it. Logged to `consent_log`.
3. **Turn loop.** STT → safety classifier → agent → TTS, streaming throughout.
4. **Resolution**, one of:
   - Task completed (appointment booked, question answered) → agent closes the call.
   - Caller requests a human, or the agent cannot help → cold transfer to 122/123.
   - **Emergency detected** → immediate transfer, bypassing all other logic, plus a parallel
     page to on-call staff.
   - Service failure or latency ceiling breach → degradation path (§10).
5. **Post-call.** Stamp `calls.ended_at`, enqueue `summarize_call`.

After transfer, the call is an ordinary call at 122/123 from the PBX's perspective. The
existing client console picks it up and records it unchanged.

**Recording of the agent-handled portion** is a separate decision (§14, open). The PBX
records legs that reach an extension normally; the agent leg is not one of those unless
explicitly configured. `call_turns` retains a turn-by-turn transcript regardless.

---

## 7. Conversation design

### 7.1 Agent structure

```
safety_check   (deterministic, pre-LLM, no model call)
   ├─ hit   → immediate transfer to 122/123 + page on-call
   └─ miss  → agent (single tool-calling LLM turn)
                tools: book_appointment
                       lookup_faq
                       collect_triage
                       request_handoff
```

One LLM round-trip per turn. Tool-calling performs the routing; the model's own text is the
spoken response. Splitting this into separate routing and response nodes costs a second
sequential model call and does not fit the latency budget — do it only if a measured problem
demands it.

**LLM access** is via OpenRouter with an ordered fallback chain (primary, then a faster
model), 8s timeout per attempt. Model IDs are configuration, selected at build time.

**Conversation state** is held in memory per call and written to `call_turns` after each
turn, off the critical path. Durable graph checkpointing is deliberately excluded from v1:
neither the SIP channel nor the media session survives a process restart, so the state it
would preserve belongs to a call that is already over. Post-hoc audit is served by
`call_turns`.

### 7.2 Safety classifier — non-negotiable requirements

**The model never decides that an emergency is not an emergency.**

- A deterministic keyword and phrase classifier runs on every user turn, in every supported
  language, **before** anything reaches the LLM.
- Categories: chest pain, breathing difficulty, unconsciousness or unresponsiveness, severe
  bleeding, stroke symptoms, suicidal ideation.
- The phrase list is authored and signed off with clinical input, not engineering judgment.
- On a hit: set `emergency_flag`, bypass all routing, transfer to a staffed extension
  immediately, write to `emergency_escalations`, and enqueue `notify_emergency` in parallel
  so on-call staff are paged even if both extensions are busy and the transfer queues.
- The list is covered by a regression suite (§13). It must not silently regress.

### 7.3 Triage — intake only

The `collect_triage` tool gathers structured symptom information and always terminates in
"please see a doctor" or a booking. It never produces a diagnosis, a severity assessment, or
reassurance that a symptom is benign. This is constrained in the system prompt **and**
verified in testing — prompt instructions alone are not a control.

---

## 8. Data model

Postgres 16+ with `pgcrypto`. PII columns are encrypted at the repository layer via
`pgp_sym_encrypt` / `pgp_sym_decrypt`, with the key held in the secrets manager — never
hardcoded, never in the same database.

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE patients (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name_encrypted    BYTEA NOT NULL,
    phone_encrypted   BYTEA NOT NULL,
    phone_hash        TEXT  NOT NULL,          -- sha256(phone); lookup without decrypting
    abha_id           TEXT,                    -- reserved, unwired
    language_pref     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX idx_patients_phone_hash ON patients (phone_hash);

CREATE TABLE calls (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id              UUID REFERENCES patients(id),
    caller_phone_hash       TEXT NOT NULL,
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at                TIMESTAMPTZ,
    language                TEXT,
    sip_call_id             TEXT,              -- correlates with PBX CDR
    disposition             TEXT,              -- resolved | transferred | degraded | dropped | emergency
    emergency_flag          BOOLEAN NOT NULL DEFAULT false,
    transferred_to_human    BOOLEAN NOT NULL DEFAULT false,
    transferred_to_extension TEXT              -- '122' | '123'
);

CREATE TABLE call_turns (
    id              BIGSERIAL PRIMARY KEY,
    call_id         UUID NOT NULL REFERENCES calls(id),
    turn_index      INT  NOT NULL,
    speaker         TEXT NOT NULL CHECK (speaker IN ('user','agent')),
    text            TEXT NOT NULL,
    stt_latency_ms  INT,
    llm_latency_ms  INT,
    tts_latency_ms  INT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_call_turns_call_id ON call_turns (call_id, turn_index);

CREATE TABLE appointments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id          UUID NOT NULL REFERENCES patients(id),
    department          TEXT NOT NULL,
    slot_start          TIMESTAMPTZ NOT NULL,
    slot_end            TIMESTAMPTZ NOT NULL,
    status              TEXT NOT NULL DEFAULT 'booked',
    created_via_call_id UUID REFERENCES calls(id)
);

CREATE TABLE consent_log (
    id           BIGSERIAL PRIMARY KEY,
    call_id      UUID NOT NULL REFERENCES calls(id),
    consent_type TEXT NOT NULL,                -- 'disclosure_played' | 'data_processing'
    granted      BOOLEAN NOT NULL,
    recorded_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE emergency_escalations (
    id                BIGSERIAL PRIMARY KEY,
    call_id           UUID NOT NULL REFERENCES calls(id),
    trigger_category  TEXT NOT NULL,           -- cardiac | breathing | bleeding | stroke | self_harm
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    human_notified_at TIMESTAMPTZ,
    notified_channel  TEXT                     -- sms | whatsapp | pager
);

CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    actor         TEXT NOT NULL,               -- 'system' | staff user id
    action        TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id   TEXT,
    call_id       UUID REFERENCES calls(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**Redis** holds the arq queue and per-phone rate limiting only. It is not a source of truth.
Per-call state lives in the agent worker process.

---

## 9. Compliance and privacy

- **DPDP Act 2023** governs this. Voice audio and derived transcripts are personal data, and
  medical context makes them sensitive.
- **Audio must remain on hospital-controlled infrastructure** (§5.3). This is the reason
  LiveKit is self-hosted rather than using a managed offering.
- **Disclosure, not an interactive consent gate.** A caller experiencing a cardiac event must
  not have to clear a consent prompt before speaking. The recorded disclosure (§6, step 2) is
  interruptible and is logged. **This resolution requires the compliance owner's sign-off.**
- PII encrypted at rest (§8), TLS in transit, SRTP available on the SIP media path.
- **No PII in application logs** — phone numbers and names are redacted at the logging layer,
  verified by test (§13).
- Retention window defined and enforced by a scheduled deletion job. Data deletion requests
  supported through the admin API.
- Access to `audit_log` and `emergency_escalations` restricted in production.
- None of this is legal advice. It requires review by whoever owns hospital compliance
  before go-live.

---

## 10. Reliability and degradation

Sarvam is a single point of failure for both STT and TTS; OpenRouter mitigates its own risk
through the model fallback chain. Neither may produce a dead line.

**Trigger:** any Sarvam or OpenRouter failure, or any turn exceeding the 2.0s hard ceiling.

**Behaviour:** play a pre-rendered audio file (`hold.pcm` — "one moment, connecting you to
our staff"), immediately transfer to 122/123, set `disposition = 'degraded'`, emit an alert.

A graceful transfer is an acceptable outcome. Silence is not.

Additional requirements:
- STT/TTS websocket reconnect with exponential backoff on transport-level closes; on auth or
  quota errors, do not blind-retry — surface and degrade.
- Rate limit inbound triggers per `phone_hash` (60s token bucket).

---

## 11. Deployment

### 11.1 Topology

Deployed on the existing Dokploy/Contabo infrastructure, reusing the pattern already in use
for HRMS. Services: LiveKit server, LiveKit SIP, Redis, Postgres, agent worker, FastAPI app,
arq worker. Sized 4–8 core / 8–16GB initially; resized against load test results (§13).

The agent worker must be reachable from the LiveKit server, and the SIP service from the
PBX. No public HTTPS endpoint is required if LAN-only operation is confirmed at Gate 0.

### 11.2 Scaling

**v1 runs a single agent worker instance and scales vertically.** For one hospital's inbound
volume this is sufficient, not a compromise. The arq worker scales independently and
statelessly.

Multi-instance is deferred because it needs real design — concurrent workers must not both
claim the same call, and the SIP trunk points at one destination. Revisit only if load
testing shows a single instance cannot absorb peak volume.

---

## 12. Observability

Wired into the existing Grafana / Loki / Tempo / Alloy stack — no parallel stack.

- `structlog` with `call_id` bound to every log line for the call's duration. PII redacted.
- OpenTelemetry spans around each STT, LLM, and TTS call, exported to Tempo.
- Per-turn `stt_latency_ms` / `llm_latency_ms` / `tts_latency_ms` written to `call_turns`,
  so latency trends are queryable in SQL.
- **Alerts:** turn latency p95 over budget; STT/TTS reconnect rate; `disposition='degraded'`
  rate; **emergency escalation not acknowledged within N minutes**.

---

## 13. Testing

| Layer | Requirement |
|---|---|
| **Safety regression suite** | Every phrase in the clinical list, in every language, asserts a transfer. Runs in CI. Non-optional — this is the liability surface. |
| **Triage constraint tests** | Adversarial prompts confirming the agent never diagnoses, never reassures, never assesses severity. |
| **Pipeline integration** | A synthetic call driver exercising the full STT→LLM→TTS path in CI with no PBX. |
| **Log redaction** | Automated grep for phone numbers and names in log output. Zero hits required. |
| **Language coverage** | Real Tamil, Hindi, English and Tanglish audio at telephony bandwidth — not English-only, not studio recordings. |
| **Barge-in under jitter** | Repeated mid-sentence interruption over the real network, not localhost. |
| **SIP load** | SIPp against the PBX's agent route, to catch channel-setup and trunk failures that API-level tests cannot. |
| **Transfer under load** | Simultaneous emergency-trigger calls; every one must land at 122/123, none silently dropped. |
| **Concurrency limits** | Verify against Sarvam's documented streaming limits before committing to a volume plan. |
| **Capacity** | Load test at 2× expected peak concurrent calls. |

---

## 14. Milestones and gates

Each gate is observable. No milestone starts until the previous gate passes.

### Gate 0 — Validate the unknowns (no product code)

| # | Spike | Gate |
|---|---|---|
| S1 | PBX identification and capability: platform, version, whether it can originate a static-IP SIP trunk, whether it accepts inbound SIP REFER. | Documented. |
| **S2** | **Transport validation.** Stand up self-hosted LiveKit + SIP + Redis, point a PBX trunk at it, run the Sarvam plugin starter agent. In parallel, obtain the compliance decision on self-hosting. | **A real call reaches the agent and transfers to 122 via REFER.** Plus: LAN-only networking confirmed without public 5060/10000–20000, and compliance sign-off on the data-residency design. **This gate selects the architecture (§5.1 or §5.5).** |
| S3 | ASR quality. ~20 real utterances captured off the PBX across all four language modes, scored by hand. | Acceptable accuracy on code-mixed speech, and a confirmed answer on required sample rate. Sets the §4 threshold. |
| S4 | Latency floor. Script STT → OpenRouter → TTS from the hospital's network, no telephony. | p50/p95 measured. If p95 exceeds 1.5s here, revisit model selection before building. |
| S5 | Sarvam concurrency limits and cost per call-minute at expected volume. | Documented; hospital signs off on cost. |

**All five answered in writing. S3 and S4 are not skippable.**

### M1 — One call, one canned turn
SIP trunk, agent worker, Sarvam STT/TTS, hardcoded reply. No LLM.
**Gate:** dial the agent route, speak, hear a fixed sentence back. Latency logged.

### M2 — Barge-in
Tune endpointing delay and interruption thresholds per Sarvam's production guidance.
**Gate:** 20 mid-sentence interruptions, agent stops within 150ms every time, no stale
audio. Kept as its own milestone because "the framework handles it" is the assumption the
architecture rests on — it gets proven, not trusted.

### M3 — The agent
Single tool-calling LLM turn, OpenRouter fallback chain, sentence-boundary TTS streaming.
**Gate:** book an appointment end to end by voice, p95 latency inside §5.4.

### M4 — Safety and handoff
Deterministic classifier, `emergency_escalations`, transfer to 122/123, `notify_emergency`.
**Gate:** every clinical phrase triggers transfer within one round-trip. Regression suite
green. Existing console recording confirmed intact on the transferred leg.

### M5 — Persistence and compliance
Domain tables, pgcrypto at the repository layer, `call_turns` off the critical path,
`audit_log`, disclosure preamble, retention job.
**Gate:** log redaction test passes. Retention job deletes on schedule.

### M6 — Observability and degradation
structlog + OTel into Grafana/Tempo. Degradation path (§10).
**Gate:** kill Sarvam mid-call — the caller gets the hold prompt and a transfer, never
silence. Alerts fire on a synthetic breach.

### M7 — Load, sign-off, cutover
Full §13 suite, then §15.
**Gate:** §15 complete. The incoming-route repoint is the **last** action, performed only
after the agent route and the transfer path are both verified in isolation, with a
documented one-line rollback.

---

## 15. Launch checklist

- [ ] Clinical review and sign-off on the emergency phrase list and triage constraints
- [ ] Compliance sign-off on the disclosure design and data-residency architecture
- [ ] PII encryption verified end to end — columns, transit, log redaction
- [ ] Retention policy defined and the deletion job running
- [ ] Emergency escalation tested against the real on-call channel, with a human confirming
      receipt
- [ ] Barge-in verified under real network jitter
- [ ] Transfer to 122/123 verified on the production PBX, including confirming the existing
      client console still records the transferred leg
- [ ] Degradation path verified — induced provider outage ends at a human
- [ ] Load tested at 2× expected peak
- [ ] Runbook written for on-call engineering when the agent misbehaves mid-call
- [ ] Rollback procedure documented and rehearsed
- [ ] **Incoming route repointed to the agent** — last, after everything above

---

## 16. Open decisions

Owner is the hospital unless noted.

0. **Self-hosted LiveKit approved?** (§5.3, §9) Decides whether patient audio ever leaves
   hospital infrastructure, and therefore which architecture is built. **Blocks Gate 0-S2 and
   most of this document.** — *Compliance owner*
1. **PBX platform, version, and REFER capability.** — *Engineering, Gate 0-S1*
2. **Is audio recording of the agent-handled leg required**, or is the `call_turns`
   transcript sufficient for DPDP and clinical purposes? Determines whether explicit
   recording must be configured on the agent route. — *Compliance + clinical*
3. **What happens when both 122 and 123 are busy?** Cold transfer lands the caller in the
   PBX's existing queue or voicemail with no agent remaining. Confirm current behaviour and
   whether it is acceptable for an escalated emergency — it may force warm transfer. —
   *Hospital operations*
4. **Expected peak concurrent calls and monthly minutes.** Sizes capacity and cost. —
   *Hospital operations*
5. **Who owns clinical sign-off, and are they available before M4?** §7.2 makes this a hard
   dependency, not a formality. — *Hospital*
6. **Disclosure vs. interactive consent** — does compliance accept the §9 resolution? —
   *Compliance owner*
