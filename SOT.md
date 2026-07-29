# STARCOOP Bidding Bot — Source of Truth

*The single authoritative record of what we are building, what we know, what we have
decided, and what is still open. If a statement here conflicts with anything else in the
repository, this document wins — or this document is wrong and must be fixed.*

| | |
|---|---|
| **Project** | Automated bidding agent for SECOP II electronic reverse auctions |
| **Owner** | Juan Loaiza |
| **Beneficiary** | COOPERATIVA DE VIGILANTES STARCOOP CTA. (company code `700961121`) |
| **Repository** | `git@github.com:juanloaiza21/starcoop.git` |
| **Status** | 🟡 Planning complete — implementation not started |
| **Last updated** | 2026-07-28 |

---

## 1. What this is, in one paragraph

STARCOOP is a Colombian security-guard services cooperative that wins revenue by
competing for public contracts. Many of those contracts are awarded through SECOP II
*subastas inversas electrónicas* — electronic reverse auctions where qualified bidders
undercut each other in a ten-minute window and the lowest compliant price wins. Today a
human sits in front of a browser and clicks. That human gets disconnected, loses track of
the arithmetic under time pressure, and cannot reliably judge when to stop. This project
replaces that human's *hands* — not their judgement — with software that stays connected
when most of the room cannot, always computes a legal bid, and is physically incapable of
bidding below a floor price that a human set in advance.

It is worth being precise about where the advantage actually comes from, because the
obvious answer is wrong. The platform itself is slow: a bid submission costs two round
trips whose 90th percentile is around seven seconds each (§4.5). Reacting in milliseconds
buys nothing. The edge is **reliability and discipline** — never dropping out, never
miscalculating, never chasing a price below cost — not raw speed.

**Read [`docs/00-context.md`](docs/00-context.md) first if you are new to the domain.**

---

## 2. Documentation map

The plan is split into chapters. Each is authoritative for its own subject.

| Chapter | Subject | Read it when |
|---|---|---|
| [`docs/00-context.md`](docs/00-context.md) | Business context, SECOP II, the captured auction, KPIs | You are new to the project |
| [`docs/01-api-contract.md`](docs/01-api-contract.md) | Reverse-engineered API, auto-generated from the captures | You are writing client code |
| [`docs/02-auction-mechanics.md`](docs/02-auction-mechanics.md) | The rules engine: decrements, extra time, state machines | You are implementing bid legality |
| [`docs/03-client-protocol.md`](docs/03-client-protocol.md) | SignalR, clock sync, reconnection, authentication | You are building the transport |
| [`docs/04-strategy-engine.md`](docs/04-strategy-engine.md) | What to bid, when, and when to stop | You are implementing decisions |
| [`docs/05-compliance.md`](docs/05-compliance.md) | Colombian law, platform ToS, guardrails | Before you touch anything live |
| [`docs/06-tender-discovery.md`](docs/06-tender-discovery.md) | Finding and qualifying auctions worth entering | You are working upstream of the auction |
| [`docs/07-architecture.md`](docs/07-architecture.md) | The decided system design | You are building anything |
| [`docs/08-roadmap.md`](docs/08-roadmap.md) | Phases, ticket backlog, open questions, decision log | You are picking up work |
| [`docs/09-platform-vocabulary.md`](docs/09-platform-vocabulary.md) | The platform's own string table: every bid-validation error key | You are handling API errors |
| [`fixtures/README.md`](fixtures/README.md) | Replayable auction trace and bid-legality conformance vectors | You are writing tests |

---

## 3. Where the knowledge came from

Everything we know about the platform was reverse-engineered from an evidence bundle
captured during a **real auction on 2026-03-02**: tender `SASI-001-2026`, buyer
*Instituto Distrital de Protección y Bienestar Animal*, single item "MEDIOS
TECNOLÓGICOS", start price **115 670 523 COP**, scheduled 10 minutes, with roughly two
dozen bidder companies connected.

The bundle contained five HAR captures, a 65-minute screen recording, and a decoded JWT.
`tools/digest_har.py` distils the HARs into [`docs/01-api-contract.md`](docs/01-api-contract.md),
redacting credentials as it goes.

> ### ⚠️ Security incident — act on this
> The evidence bundle contained a **live session JWT** (`TOKENjson.txt`) and HAR files
> carrying session cookies for the STARCOOP SECOP II account. Treat those credentials as
> **compromised**.
>
> - **Rotate the SECOP II password for user `Starcoopcta2017` before going live.**
> - The raw bundle is **gitignored** (`*.har`, `TOKEN*.txt`, `*.mp4`, `*.rar`, `*.zip`)
>   and must never be committed.
> - Share the bundle only over channels appropriate for secrets.
>
> See [`docs/05-compliance.md`](docs/05-compliance.md).

---

## 4. The nine facts that shape the entire design

Everything in the architecture traces back to one of these.

### 4.1 It is a reverse auction — lower wins, and that is dangerous
`auctionTypeCode: "Reversed"`, `auctionEvaluationTypeCode: "BestPrice"`. A bug that bids
too high loses one contract. A bug that bids too low **wins a contract at a loss** and
binds the cooperative to deliver at that price. The failure modes are not symmetric, so
the floor price is enforced in multiple independent layers and the bot's default posture
is to do nothing. See [`docs/04-strategy-engine.md`](docs/04-strategy-engine.md).

### 4.2 Sniping does not work here — auto-extension changes the game
`extraTimeAutomaticAllow: true`, `extraTimePeriod: 3`, `extraTimeLastMinutesStartPeriod: 1`.
A bid placed inside the final minute extends the auction by three more minutes. The
auction therefore does not end at a known time; it ends when nobody is willing to improve.
Classic last-second sniping simply buys everyone else another three minutes. The winning
behaviour is disciplined, floor-aware persistence — not speed alone. See
[`docs/02-auction-mechanics.md`](docs/02-auction-mechanics.md).

### 4.3 The connection drops constantly — for everyone — and that is our opening
The screen recording shows the auction room displaying **`ESTADO: DESCONECTADO`**. That
is not an isolated glitch. Replaying the captured participant events quantifies it:

| Measure | Value |
|---|---|
| Companies connected during the auction | 44 |
| Companies that disconnected at least once | **36** |
| Total disconnect events in ~10 minutes | 100 |
| Total reconnect events | 199 |
| Median reconnects per company | 4 |
| Worst-affected competitor | 19 reconnects |

**Roughly 80% of the room was fighting the connection rather than the auction.** A
dropped socket in a ten-minute event is the difference between competing and not.

This reframes the project. The bot's edge is not primarily that it computes prices
faster than a human — it is that it *stays in the room* when most rivals cannot, and
reconciles its state correctly when it does drop. Reconnection, cursor recovery and
reconciliation against `GetSupplierBids` and the audit log are the core feature, not
error handling. See [`docs/03-client-protocol.md`](docs/03-client-protocol.md).

The numbers above are reproducible from
[`fixtures/auction-sasi-001-2026.json`](fixtures/auction-sasi-001-2026.json).

### 4.4 The server owns time, and we must measure our distance from it
The `auction.RequestClock` / `auction.ReceiveClock` pair is the authoritative clock. Local
system time is irrelevant. Every deadline decision uses a round-trip-compensated offset
against server time.

**Do not estimate the clock offset from the long-poll response.** The obvious NTP-style
midpoint estimate is invalid here: `/signalr/poll` is a *long poll* that holds the
connection open (median 1.5 s, p90 20 s, observed maximum 110 s) and the server emits the
clock at an arbitrary moment during that hold. Applying the midpoint assumption to our
capture yields offsets scattered across a 14-second range — pure noise. Offset must be
derived from the short `/signalr/send` round trip instead.

**The clock is also coarse.** Observed `ReceiveClock` ticks arrive at a median interval of
**9.3 s**, with gaps up to **30 s**. The bot therefore cannot simply wait for a tick to
learn the time: it must hold a local estimate `serverTime ≈ localClock + offset`,
extrapolate between ticks, and correct the offset when a tick lands. Treating the last
received tick as "now" would leave it up to half a minute stale at exactly the moment
precision matters.

All of the numbers in §4.4 and §4.5 are reproducible with
`python3 tools/analyze_timing.py <har-dir>`.

### 4.5 The platform is slow and wildly variable — this dominates the timing budget
Measured from the capture, by request class:

| Request class | min | p50 | p90 | max |
|---|---:|---:|---:|---:|
| `signalr/send` (short POST, true RTT) | 92 ms | **832 ms** | 3 744 ms | 9 732 ms |
| REST `/v1/*` (incl. `ValidateBid`) | 137 ms | **941 ms** | 6 861 ms | 10 067 ms |
| `signalr/connect` | 98 ms | 433 ms | 5 010 ms | 5 010 ms |

A submit is **two** sequential round trips — `ValidateBid` then `SubmitBid`. At p90 that
is roughly **14 seconds**, and the tail reaches 20.

The consequences are severe and they contradict the intuitive design:

- **Sub-second reaction speed is not the bottleneck.** Shaving milliseconds off local
  decision-making is irrelevant next to a p90 seven-second server response. Effort spent
  on micro-optimising the hot path is wasted.
- **Sniping is impossible on this platform**, independent of the auto-extension rule in
  §4.2. You cannot decide at T-2 s and expect the bid to land; the platform may not answer
  for ten seconds.
- **The bot must maintain a latency budget** — a live estimate of how long a
  validate-plus-submit cycle currently takes — and commit to its final bid while at least
  the p90 of that budget remains. Acting later is gambling.
- **Bids must be idempotent and reconciled.** With ten-second tails, a timeout does not
  mean the bid failed. The bot must resolve what actually happened via `GetSupplierBids`
  and the audit log before retrying, or it risks double-bidding against itself.

This is the finding that most changes how the runtime should be built, and it is
reproducible from [`fixtures/auction-sasi-001-2026.json`](fixtures/auction-sasi-001-2026.json).

### 4.6 The minimum decrement rule is CONFIRMED — 1% of the *initial* price
`GET /v1/auction/GetAuction/{id}/{locale}` (the full variant, richer than
`GetAuctionSimple`) returns `minimumBidType: "Percentage"` where the simple variant
returns the integer `1`, and each price-list item carries `minBidDifference: 1.0`.
One percent of the item's `itemInitialPrice` of 115 670 523 is **1 156 705,23** — exactly
the `minBid` the platform quoted when it rejected our bid. The step is therefore a
percentage of the **initial** price, a fixed absolute amount for the whole auction, not a
percentage of the current best. This is now **CONFIRMED**, not assumed.

> **Always read the full `GetAuction`, not `GetAuctionSimple`.** The simple variant
> returns opaque integer enums and omits `minBidDifference` entirely. Every rule the bot
> depends on lives in the full payload.

### 4.7 The platform deliberately anonymises competitors — respect that
Competitor records return `companyName: "Proveedor J"`, `alias: "J"` and
`companyNIF`/`companyContact`/`companyEmail` all set to the sentinel
`"Settings_Confidential"`. The platform is intentionally concealing who you are bidding
against.

However, `companyCode` **is** the real Colombian NIT, and NITs are resolvable against
public SECOP open data. The bot is therefore technically capable of de-anonymising every
rival in the room.

**It must not.** Doing so would subvert a deliberate platform privacy control, and a
bidder who knows exactly which rivals are present is a short step from the coordination
that Colombian law criminalises. The bot may use the *count* of connected bidders and
the *anonymous* price signals; it may not resolve `companyCode` to an identity. This is a
hard rule, recorded as decision **D-09** below and expanded in
[`docs/05-compliance.md`](docs/05-compliance.md).

### 4.8 The captures were sanitised — the JWT alone is not enough to authenticate
The HAR shows no `Cookie` or `Authorization` header on the REST calls, but byte-accounting
proves a constant **2 528 bytes** of headers were stripped from every single request — the
size of a VORTAL/ASP.NET session cookie jar. The JWT is only *proven* to authenticate the
SignalR transport, where it travels as a `token` query parameter. **We therefore assume
REST needs the full browser session**, which is why authentication bootstraps through a
real browser and exports complete storage state. This is a `DERIVED` conclusion and is
tracked as an open question.

---

### 4.9 The captured auction had 44 live competitors — speed alone will not save us
`GetAuction` listed **48 registered bidders**, and the SignalR `chat` roster shows
**44 distinct companies actually connected** — a 92% participation rate, fighting over a
single 115 M COP contract with a fixed 1% step.

This is the most commercially important number in the whole evidence bundle, and it
cuts against the naive framing of this project. In a 44-way race, *someone* will bid down
to their own floor. Being the fastest clicker in that room wins a contract at a margin
nobody wants. The lesson is that **auction selection matters at least as much as auction
execution**: the highest-leverage work is filtering for auctions STARCOOP can actually win
profitably — favourable geography, incumbent advantage, fewer qualified rivals — rather
than bidding harder in commodity races.

This is why [`docs/06-tender-discovery.md`](docs/06-tender-discovery.md) is not an
afterthought, and why the bot's most valuable output on many auctions will be the
decision **not to bid**.

### 4.10 Every action is logged, including failures, with our IP attached
`GET /v1/audit/GetEventLogs` returns the platform's permanent record. It contains rows
like `"Error cuando presentado el lance. Problema: ..."` — **a bid that fails validation
is not discarded, it is recorded**, along with `ipAddress` and `userCode`, where the
contracting entity can read it.

Two consequences:

- A bot that sprays speculative bids publishes a log of its own malfunction on a
  government platform under STARCOOP's name. Validation is free and leaves no trace;
  rejection is permanent. This is the real justification for decision **D-02**.
- Our network origin is attributable. The bot should run from a **stable, attributable
  connection**. Rotating or anonymising IPs would serve no legitimate purpose here and
  would look evasive in precisely the record a dispute would examine.

The audit log is also the **reconciliation source of truth** after a dropped connection:
it answers "what did I miss?" in a way the realtime stream cannot.

## 5. Decisions already made

These are settled. Reopening one requires a new entry in the decision log in
[`docs/08-roadmap.md`](docs/08-roadmap.md).

| # | Decision | Rationale |
|---|---|---|
| D-01 | **A floor price is mandatory and is enforced in independent layers** | The asymmetric failure mode in §4.1. No single bug may be able to defeat it. |
| D-02 | **`ValidateBid` always precedes `SubmitBid`** | Rejected bids are written permanently to an audit trail the buyer can read (§4.10). Validation is free and silent; rejection is public and attributed. |
| D-03 | **Browser-assisted session bootstrap** | Login is reCAPTCHA-gated and REST likely needs cookies (§4.8). Fully headless unattended auth is not achievable or honest to promise. |
| D-04 | **Read-only observer before any automated bid** | The `SubmitBid` contract was never captured. We learn it by watching a real auction and submitting nothing. |
| D-05 | **Nothing hardcoded to one auction, company or lot shape** | The user's explicit requirement that it "work universally everywhere". |
| D-06 | **Secrets never enter git; the leaked token is treated as compromised** | §3. |
| D-07 | **Polite rate limiting against a government platform** | It is a public system. Being aggressive is both wrong and the fastest route to a banned account. |
| D-08 | **A human kill switch that works instantly** | The operator must always be able to take back control. |
| D-09 | **Never resolve a competitor's `companyCode` to an identity** | The platform anonymises rivals on purpose (§4.7). Knowing who is in the room enables the coordination that is criminal in Colombia. |
| D-10 | **Read auction rules from the full `GetAuction`, never `GetAuctionSimple`** | The simple variant returns opaque integer enums and omits `minBidDifference` (§4.6). |
| D-11 | **Maintain a live latency budget and commit the final bid while at least its p90 remains** | The platform's p90 response is ~7 s per round trip and a submit costs two (§4.5). Acting later is gambling, not sniping. |
| D-12 | **Treat a timed-out submit as unknown, never as failed** | With ten-second tails, a timeout does not mean the bid did not land. Reconcile via `GetSupplierBids` and the audit log before retrying, or risk bidding against ourselves. |
| D-13 | **Derive the clock offset from `signalr/send` round trips, never from the long poll** | The long poll holds for up to 110 s and the server emits the clock at an arbitrary point within it, so the midpoint estimate is noise (§4.4). |

---

## 6. Scope

### In scope
- Automating STARCOOP's **own** bids on STARCOOP's **own** account.
- Discovering and qualifying relevant tenders upstream of the auction.
- Operating reliably across Linux, macOS, Windows and Docker.

### Explicitly out of scope
- Anything touching another bidder's account or data beyond what the API returns to us.
- Any form of coordination with other bidders. Bid rigging is a **crime** in Colombia
  (Ley 1474 de 2011, art. 27; Código Penal art. 410A). See [`docs/05-compliance.md`](docs/05-compliance.md).
- Circumventing platform security controls, including reCAPTCHA.
- Load-testing, stressing or probing SECOP II infrastructure.
- Deciding the floor price. **A human sets the floor. The bot only obeys it.**

---

## 7. Current state and what happens next

**Status: planning complete, no implementation yet.**

The immediate next actions are maintained in the "next three actions" section of
[`docs/08-roadmap.md`](docs/08-roadmap.md). The full ticket backlog and the open-questions
register live there too.

The largest unknowns blocking implementation:

| Open question | Why it blocks | How it gets resolved |
|---|---|---|
| The `SubmitBid` request/response contract | We cannot place a bid without it | Phase 2 read-only observer on a live auction |
| Whether the **first bid must equal the base price** | Costs an extra round trip before reaching a competitive price, changing the timing budget | Observation; the platform's own error string asserts it (see [`docs/09-platform-vocabulary.md`](docs/09-platform-vocabulary.md)) |
| The other `minimumBidType` values | We have confirmed `Percentage` only; `Value`/`None` presumably exist | Observe auctions with differing configurations |
| Exact REST authentication (§4.8) | Determines the session model | Fresh unsanitised capture |
| Whether extra time triggers on any bid or only a winner-changing bid | Changes end-game strategy | Observation |
| Platform ToS position on automation | Determines whether we proceed at all | [`docs/05-compliance.md`](docs/05-compliance.md) |

---

## 8. Conventions for this repository

- Every factual claim about the platform carries a confidence marker: **CONFIRMED**
  (direct evidence in the captures), **DERIVED** (arithmetic or logic from evidence), or
  **ASSUMED** (needs verification). Never silently promote a claim to a higher confidence.
- Unverified external citations are marked **UNVERIFIED**. Do not invent sources.
- Spanish domain vocabulary (*lance*, *subasta inversa*, *pliego*, *margen mínimo de
  mejora*) is used deliberately; prose is in English.
- Secrets never enter the repository. See `.gitignore`.
