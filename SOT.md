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
human sits in front of a browser and clicks. That human is slow, gets disconnected, and
cannot compute the profitable stopping point under time pressure. This project replaces
that human's *hands* — not their judgement — with software that reacts in milliseconds,
never loses the connection, and is physically incapable of bidding below a floor price
that a human set in advance.

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

## 4. The five facts that shape the entire design

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

### 4.3 The connection drops mid-auction
The screen recording shows the auction room displaying **`ESTADO: DESCONECTADO`**. In a
ten-minute event, a dropped SignalR socket is the difference between winning and not
competing at all. Reconnection, cursor recovery and state reconciliation against
`GetSupplierBids` are first-class requirements, not error handling.
See [`docs/03-client-protocol.md`](docs/03-client-protocol.md).

### 4.4 The server owns time, and we must measure our distance from it
The `auction.RequestClock` / `auction.ReceiveClock` pair is the authoritative clock. Local
system time is irrelevant. Every deadline decision uses a round-trip-compensated offset
against server time.

### 4.5 The minimum decrement rule is CONFIRMED — 1% of the *initial* price
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

### 4.6 The platform deliberately anonymises competitors — respect that
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

### 4.7 The captures were sanitised — the JWT alone is not enough to authenticate
The HAR shows no `Cookie` or `Authorization` header on the REST calls, but byte-accounting
proves a constant **2 528 bytes** of headers were stripped from every single request — the
size of a VORTAL/ASP.NET session cookie jar. The JWT is only *proven* to authenticate the
SignalR transport, where it travels as a `token` query parameter. **We therefore assume
REST needs the full browser session**, which is why authentication bootstraps through a
real browser and exports complete storage state. This is a `DERIVED` conclusion and is
tracked as an open question.

---

## 5. Decisions already made

These are settled. Reopening one requires a new entry in the decision log in
[`docs/08-roadmap.md`](docs/08-roadmap.md).

| # | Decision | Rationale |
|---|---|---|
| D-01 | **A floor price is mandatory and is enforced in independent layers** | The asymmetric failure mode in §4.1. No single bug may be able to defeat it. |
| D-02 | **`ValidateBid` always precedes `SubmitBid`** | The platform will tell us if a bid is illegal. Never guess. |
| D-03 | **Browser-assisted session bootstrap** | Login is reCAPTCHA-gated and REST likely needs cookies (§4.5). Fully headless unattended auth is not achievable or honest to promise. |
| D-04 | **Read-only observer before any automated bid** | The `SubmitBid` contract was never captured. We learn it by watching a real auction and submitting nothing. |
| D-05 | **Nothing hardcoded to one auction, company or lot shape** | The user's explicit requirement that it "work universally everywhere". |
| D-06 | **Secrets never enter git; the leaked token is treated as compromised** | §3. |
| D-07 | **Polite rate limiting against a government platform** | It is a public system. Being aggressive is both wrong and the fastest route to a banned account. |
| D-08 | **A human kill switch that works instantly** | The operator must always be able to take back control. |
| D-09 | **Never resolve a competitor's `companyCode` to an identity** | The platform anonymises rivals on purpose (§4.6). Knowing who is in the room enables the coordination that is criminal in Colombia. |
| D-10 | **Read auction rules from the full `GetAuction`, never `GetAuctionSimple`** | The simple variant returns opaque integer enums and omits `minBidDifference` (§4.5). |

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
| Exact REST authentication (§4.7) | Determines the session model | Fresh unsanitised capture |
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
