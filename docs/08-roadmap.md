# 08 — Delivery Plan and TODO Backlog

*The execution half of the Source of Truth: phased roadmap, ticket backlog, open-questions register, decision log, and the next three actions — everything here is subordinate to the architecture decisions recorded in the project root document and the sibling chapters.*

Cross-references: business context ([00-context.md](./00-context.md)) · API contract ([01-api-contract.md](./01-api-contract.md)) · auction rules engine ([02-auction-mechanics.md](./02-auction-mechanics.md)) · wire protocol ([03-client-protocol.md](./03-client-protocol.md)) · strategy and floor defence ([04-strategy-engine.md](./04-strategy-engine.md)) · legal framework ([05-compliance.md](./05-compliance.md)) · discovery funnel ([06-tender-discovery.md](./06-tender-discovery.md)).

---

## 1. How to read this plan

Three rules govern sequencing, all inherited from the architecture:

1. **Safety before capability.** No phase that can write to SECOP begins until every phase that only reads has retired its risks. The bot's first live contact with a real auction is as a pure observer (Phase 2), and its first "product" is the zero-write co-pilot — not an armed bidder.
2. **Open questions gate phases, not tickets.** Where a phase's exit criteria depend on an unresolved question (O2 SubmitBid contract, OQ-03 decrement semantics), the phase can *start* but cannot *exit*. The register in §4 maps each blocker to its resolution path.
3. **What is not tested offline does not exist** (Principle 10). Every phase ships its simulator coverage in the same phase, not later. CI never touches `*.secop.gov.co`.

Priorities: **P0** = blocks the critical path to a safe first live bid; **P1** = required for production operation; **P2** = valuable, deferrable. Sizes: **S** ≤ 1 day, **M** 2–4 days, **L** ≥ 1 week.

---

## 2. Phased roadmap

### Phase 0 — Foundations (repo, CI, contract, secrets, incident response)

**Goal.** A repository in which unsafe code is mechanically difficult to write, plus closure of the credential-leak incident from the evidence-gathering campaign.

**Deliverables.**
- Credential rotation executed and documented: the HAR-captured JWT, session cookies, and account password treated as compromised per [05-compliance.md](./05-compliance.md) incident scenario 1. No further authenticated work until done.
- Repo scaffold with the enforced layer layout (`domain/`, `strategy/`, `guard/`, `submit/`, `app/`, `adapters/`, `session/`, `discovery/`, `audit/`, `console/`, `notify/`); `uv` project; multi-arch Dockerfile.
- CI: `mypy --strict`, `ruff` with custom rules (banned tokens `float`, `time.time()` in safety layers), `import-linter` contracts, `pytest` with a network-egress guard that fails any test opening a socket to a non-localhost address.
- Typed API contract: frozen `pydantic` v2 models for every request/response shape in [01-api-contract.md](./01-api-contract.md), generated-then-hand-verified against the HAR corpus; `Centavos` NewType and the es-CO money codec (PyICU, `ROUND_CEILING`).
- Config schema: per-auction YAML (auction identity, mode, strategy, cadences) and the signed FloorSheet format from [06-tender-discovery.md](./06-tender-discovery.md).
- Secret handling: OS keychain / Docker secrets; `structlog` JWT/`token=` redaction processor installed on every handler and covered by a test that asserts a token never survives into a log line.
- Hash-chained audit JSONL writer (`prev_sha256`, `fsync` on bid path) with a standalone chain verifier.
- Fixture pipeline: `digest_har.py` / `build_replay_fixture.py` outputs versioned into `tests/fixtures/` as the canonical replay corpus.

**Exit criteria.** CI green on a clean clone with zero network access; a deliberately-introduced `float` in `guard/` fails lint; a deliberately-broken audit chain fails the verifier; password rotated and rotation logged.

**Risks retired.** Leaked-credential exposure; wire-shape drift going unnoticed; float contamination of money math; secrets in logs or git history.

---

### Phase 1 — Session bootstrap (human-assisted auth)

**Goal.** A reliable, human-assisted path from "operator sits down" to "runner holds a FRESH token and a warm browser context", with expiry handled loudly.

**Deliverables.**
- Browser process: Playwright headful persistent context in a **separate OS process**, supervised over JSON-IPC (`bootstrap`, `snapshot_dom`, `canary`, `prefill`, `submit`, `focus`), every command with hard timeout.
- `centinela login`: navigates to the auction room, human solves reCAPTCHA, JWT sniffed from the `token=` query param of the first `/signalr/negotiate`, full `storage_state` exported, token stored in keychain.
- Token FSM (`FRESH`/`STALE`/`EXPIRED`), refresh attempt at `exp − 600 s`, instant `EXPIRED` on any 401/403, all-channel alarm when refresh hits reCAPTCHA — never a silent failure.
- Three-strategy REST-auth self-test (JWT-only header, JWT + cookies, cookies-only) run at startup, failing closed — this is the experiment that resolves OQ-01.
- `(auctionId, companyCode)` exclusive lockfile; `companyCode` read from the JWT claim, never from config.

**Exit criteria.** Operator can bootstrap a session in < 5 minutes; self-test reports which auth recipe REST actually requires; a token expired mid-session produces an alarm within 10 s in a supervised test; no credential material appears in any log or fixture.

**Risks retired.** OQ-01 (REST auth recipe); silent auth death mid-auction; the "headless unattended login" dead end (accepted as impossible by design, per hard constraint 5).

---

### Phase 2 — Read-only observer (`centinela watch` + co-pilot)

**Goal.** Attend real auctions end-to-end with zero write capability, producing the evidence that resolves the two most dangerous unknowns (SubmitBid contract, decrement semantics) and hardening every read-path component against the platform's confirmed pathologies.

**Deliverables.**
- REST leg: clients for `GetAuction`, `GetAuctionSimple`, `GetSupplierBids` (paginate-until-empty), `GetEventLogs`, `GetAllMessages`, with phase-based cadence and the global politeness token bucket.
- Hand-rolled SignalR leg: negotiate → connect → start → poll → send → abort; cursor `C` persisted before `M` dispatch; `G` persisted; exactly one poll in flight; `T:1`/`D:1` handling with reconnect budget; clean `abort` on shutdown.
- ServerClock: min-delay filter over `ReceiveClock`↔`/send`-RTT samples, θ ± ε, pessimistic edge, 7-fractional-digit ISO parser, gates (ε > 2 s, age > 60 s → freeze + alarm), `Date`-header degradation rung.
- State store with `StateHighWater` regression guards, two-source corroboration, event-log transition authority, fail-closed on unrecognized state strings.
- Decision loop skeleton (single-writer queue, watchdog OS thread, heartbeat) running the `shadow` strategy only.
- **Co-pilot mode** (`centinela copilot`): full read stack + guard + strategy, rendering the next legal floor-clamped bid with one-key copy to clipboard. This is the first shippable product and is used at the very next real STARCOOP auction.
- Evidence missions run during real auctions: (a) record a **human-placed manual bid** end-to-end to pin the SubmitBid contract (OQ-02); (b) observe ≥ 3 auctions with differing `minimumBidType` to settle decrement semantics (OQ-03); (c) a read-only `negotiate` probe for `TryWebSockets` (OQ-05); (d) observe whether non-winner-changing bids trigger extensions (OQ-04).

**Exit criteria.** One full real auction observed start-to-finish with 100% event capture, clock ε ≤ 0.5 s sustained, zero politeness violations, and an audit chain that replays deterministically; co-pilot used live by the operator at least once; OQ-02 raw capture in hand.

**Risks retired.** SignalR wire-format guesswork (03-client-protocol's byte-level open question); clock skew; backwards state reads; WAF/politeness blocking; and — critically — it converts OQ-02/OQ-03 from speculation into recorded evidence without ever writing.

---

### Phase 3 — Simulator + strategy engine (offline)

**Goal.** Every safety invariant and every strategy behavior proven against replayed reality and adversarial orderings before any write code exists.

**Deliverables.**
- `vortal-sim`: in-repo ASGI simulator speaking the recorded REST + SignalR dialect, driven by the Phase-0 fixtures, including the SASI-001-2026 65-minute recording as a golden replay.
- Pathology injection: backwards lifecycle reads, +13.15 s skew, `T:1` storms, mid-auction 401, es-CO `minBid` strings, `endDate` mutation on extension, 100-disconnect storms.
- Adversary personas: probe-bidders (1,00 / 0,00 COP), aggressive stepper, last-minute extender, stalemate.
- `AuctionView` projection with probe-bid filtering; strategy library per [04-strategy-engine.md](./04-strategy-engine.md): `minimal_step`, `patient_endgame`, `adaptive`, mandatory bracket envelope; all pure, deterministic, no I/O.
- Hypothesis property suites: never emit a bid below floor (unrepresentable at the type level), never bid at rank 1, deterministic replay, monotone descent, invariance under event reordering/duplication.
- `test_hotpath_latency.py`: decide→guard→serialize ×10 000 over the replay fixture, build fails if p99 > 5 ms.

**Exit criteria.** Golden replay reproduces the real auction's observable timeline exactly; all property suites green over ≥ 10⁵ generated cases; hot-path gate enforced in CI; a seeded simulator tournament shows `minimal_step` never violates an invariant under any persona.

**Risks retired.** Strategy logic errors; probe-bid chasing; floor arithmetic edge cases; the "is Python fast enough" question (measured, not assumed).

---

### Phase 4 — Guarded live bidding

**Goal.** The first automated lance, placed under five independent floor layers, a latched kill switch, and a human who typed a confirmation — after a dry-run ladder proves each rung.

**Deliverables.**
- `guard/` package (independently reviewed, separately version-pinned): L1 BidGuard, L2 type-level clamp, L3 byte-level floor re-verification on the serialized body, L4 post-submit detection with mandatory auto-kill, atop L0 signed FloorSheet.
- SubmitGateway: `SetStartBiddingAudit` → `ValidateBid` → driver → reconcile; non-reentrant; `ValidatedBid` TTL 5 s; byte-identical body reuse; ≤ 2 ValidateBid rejections per auction; kill-check immediately pre-submit; `AMBIGUOUS` → reconcile, never blind retry.
- Latched kill switch (file + signal + panel + Telegram), checked at 4 Hz and pre-submit; watchdog starvation alarms and surfaces TOMAR CONTROL but does not auto-kill (floor violations excepted).
- Browser submit driver (rung 2) with selector canary at ARM and on ENDGAME entry; `adapters/secop/selectors.yaml`; DOM-fingerprint check.
- REST SubmitBid driver (rung 1) implemented against the OQ-02 capture but gated behind `submit.confirmed: false` until pinned.
- Reconciliation routine on all five triggers, `RECONCILE` audit records; crash-recovery journal replay on restart.
- Mode ladder with typed confirmations: `shadow → copilot → assisted → armed`; dry-run mode (full cycle with stubbed drivers) rehearsed against the live platform's read path.
- First armed run in a Subasta de Prueba if access is confirmed (OQ-13), else in the lowest-stakes qualifying real auction, at `minimal_step`, floors set conservatively high, operator watching with TOMAR CONTROL ready.

**Exit criteria.** One real lance placed by the bot, validated first, above floor, inside the two-rejection budget, with a complete fsynced audit chain; kill switch tripped and verified latched in a live drill; L4 detection exercised in dry-run; compliance sign-offs from [05-compliance.md](./05-compliance.md) tier 1–3 checklists complete.

**Risks retired.** The company-destroying wrong bid (five layers + drills); ambiguous submit outcomes; mid-auction DOM drift; unauthorized automation (a named human's typed confirmation precedes every armed session).

---

### Phase 5 — Tender discovery and qualification pipeline

**Goal.** A daily funnel from open data to a human-approved `AuctionCard` + `FloorSheet`, so auctions are entered deliberately, days ahead, with a lawful floor already computed.

**Deliverables.** Per [06-tender-discovery.md](./06-tender-discovery.md): Socrata clients with the verified SoQL recipes; UNSPSC `V1.92121700` ∪ keyword filter; pliego download and margen-mínimo extraction; Supervigilancia tariff schedule as Layer-0 floor input; `PipelineLead → AuctionCard → FloorSheet → PortalHandoff` data model with the mandatory human FloorSheet approval gate (D2); rival-roster analytics with explicit "do not enter" recommendations; `centinela scan` as cron/launchd entry.

**Exit criteria.** Pipeline surfaces a real upcoming SASI ≥ 3 days before auction day with a draft FloorSheet whose tariff layer matches a hand computation to the centavo; a "do not enter" recommendation produced and reviewed for at least one crowded tender.

**Risks retired.** Missed tenders; floor sheets improvised on auction morning; entering unwinnable 44-rival fights.

---

### Phase 6 — Operator UI, notifications, reporting

**Goal.** The whole system operable by a security cooperative: three actions and one red button, alarms that cannot be missed, and evidence-grade reporting after every auction.

**Deliverables.** FastAPI + SSE panel (countdown on the disciplined clock, both state machines, `endDate` vs `originalEndDate`, serious/probe-marked leaderboard, leg health per rung, read-only floor sheet with SHA, next legal bid, ARM/DISARM with typed confirmation, latched KILL, TOMAR CONTROL); noVNC browser takeover ≤ 2 s; Telegram notifier (T−30 readiness, ARMED, every bid, rank change, extension, degradation, `SKIP_NO_TIME`, kill, deadman) with `/kill` as the only inbound command; post-auction report generator replaying the audit chain into a human-readable Spanish summary; Spanish runbooks for bootstrap, auction day, kill drill, and incident response.

**Exit criteria.** A non-developer operator runs a full auction (co-pilot or armed) from the panel alone, using only the runbooks; a surprise kill drill and a deadman drill both pass; every alarm path verified end-to-end on a phone.

**Risks retired.** Operator error under time pressure; alarms lost in a noisy channel; unprovable authorization chain.

---

### Phase 7 — Multi-platform generalisation (Vortal/NextWay beyond Colombia)

**Goal.** Factor the CO1-specific surface into a marketplace adapter so the engine serves other Vortal/NextWay deployments (the platform vendor operates near-identical stacks in other jurisdictions).

**Deliverables.** Marketplace configuration abstraction (hosts, locale, currency, money codec, selectors, politeness budget per platform); survey of at least one non-CO1 Vortal deployment's negotiate/REST surface (read-only, ToS-checked first); per-jurisdiction compliance annex template mirroring [05-compliance.md](./05-compliance.md); multi-locale money codec generalisation.

**Exit criteria.** The CO1 adapter contains every SECOP-specific string in the codebase (verified by the import-linter/ruff URL rules); the simulator can be parameterised to a second marketplace profile; a written legal go/no-go exists before any live contact with a new platform.

**Risks retired.** Hardcoded-to-SASI-001-2026 rot (hard constraint 1); accidental ToS breach on a new jurisdiction.

---

## 3. TODO backlog

| ID | Title | Phase | Pri | Size | Depends on | Acceptance criteria |
|---|---|---|---|---|---|---|
| SC-001 | Rotate SECOP account password; declare HAR-captured JWT + cookies compromised; document rotation | 0 | P0 | S | — | Password rotated; old JWT confirmed expired/revoked; incident note in audit repo; no authenticated tooling run before completion |
| SC-002 | Repo scaffold with enforced layer layout, `uv`, Dockerfile (multi-arch) | 0 | P0 | S | — | Clean clone builds; layout matches architecture §4; `uv run pytest` green |
| SC-003 | CI pipeline: mypy --strict, ruff, pytest, coverage, no-network egress guard for tests | 0 | P0 | M | SC-002 | CI fails on type error, lint error, or any test socket to non-localhost; badge green |
| SC-004 | import-linter contracts encoding layer rules (domain imports nothing from adapters; strategy cannot import guard internals; SECOP URLs only in adapters/secop) | 0 | P0 | S | SC-002 | A violating import in a scratch branch fails CI with a named contract |
| SC-005 | ruff custom rules banning `float` and `time.time()` tokens in domain/, guard/, strategy/, submit/ | 0 | P0 | S | SC-003 | Injected `float` literal in guard/ fails lint; logging module exempt |
| SC-006 | `Centavos = NewType` + es-CO money codec (PyICU parse/format, ROUND_CEILING only) with exhaustive round-trip tests incl. `"1.156.705,23 COP"` | 0 | P0 | M | SC-002 | Round-trips all HAR-observed money strings; property test: parse∘format = id; ceiling rounding proven at boundaries |
| SC-007 | Typed API contract: frozen pydantic v2 models for all endpoints in 01-api-contract.md, validated against raw HAR bodies | 0 | P0 | L | SC-006 | Every captured request/response parses; unknown-field drift raises at adapter boundary; models are frozen |
| SC-008 | Config schema: per-auction YAML + signed FloorSheet format with SHA display string | 0 | P0 | M | SC-007 | Schema-validated load; tampered FloorSheet signature rejected; example configs in repo |
| SC-009 | Secrets: keychain/Docker-secret storage + structlog JWT/`token=` redaction processor on all handlers | 0 | P0 | M | SC-002 | Test proves a JWT written through every log path is redacted; secret never in env dumps or git |
| SC-010 | Hash-chained audit JSONL writer (prev_sha256, fsync on bid path) + standalone chain verifier CLI | 0 | P0 | M | SC-002 | Broken chain detected; fsync measured < 10 ms; verifier replays SASI fixture chain |
| SC-011 | Fixture pipeline: run digest_har.py / build_replay_fixture.py, version canonical fixtures into tests/ | 0 | P0 | M | SC-002 | Fixtures deterministic, redacted (no live tokens), cover all 5 HARs + recording timeline |
| SC-012 | VPS provisioning: single stable attributable egress IP, WireGuard to operator laptop, Docker runtime | 0 | P1 | M | — | Egress IP static and documented; panel reachable only via WireGuard; reboot-safe |
| SC-013 | Browser OS-process supervisor: Playwright persistent context behind JSON-IPC (bootstrap/snapshot_dom/canary/prefill/submit/focus), hard timeouts, watchdog | 1 | P0 | L | SC-002 | Wedged-browser fault injection stalls only browser commands, never the caller; IPC commands time out cleanly |
| SC-014 | `centinela login`: headful bootstrap, human reCAPTCHA, JWT sniff from first /signalr/negotiate `token=`, storage_state export to keychain | 1 | P0 | M | SC-013, SC-009 | End-to-end login < 5 min; token + storage_state persisted encrypted; nothing sensitive logged |
| SC-015 | Token FSM (FRESH/STALE/EXPIRED): refresh at exp−600 s, instant EXPIRED on 401/403, loud captcha alarm | 1 | P0 | M | SC-014 | Simulated expiry drives correct transitions; captcha-blocked refresh raises all-channel alarm within 10 s; zero silent retries |
| SC-016 | Three-strategy REST-auth self-test (JWT header / JWT+cookies / cookies), fail closed, run at startup — resolves OQ-01 | 1 | P0 | M | SC-014, SC-007 | Self-test reports the working recipe against live GetAuctionSimple; startup aborts if none works; result recorded in audit |
| SC-017 | companyCode from JWT claim only + exclusive lockfile on (auctionId, companyCode) | 1 | P0 | S | SC-014 | Config-supplied companyCode rejected; second runner on same pair exits with clear error |
| SC-018 | REST clients: GetAuction, GetAuctionSimple, GetSupplierBids (paginate until empty), GetEventLogs, GetAllMessages | 2 | P0 | M | SC-007, SC-016 | All responses parse into frozen models; pagination proven on multi-page fixture; per-call latency recorded to SQLite |
| SC-019 | SignalR handshake: negotiate → connect → start with byte-identical connectionData, percent-encoded connectionToken, JWT as `token` query param | 2 | P0 | L | SC-016 | Byte-level match against raw HAR of every handshake request; verb/param placement confirmed per 03-client-protocol first-task flag |
| SC-020 | SignalR cursor threading and message deduplication: persist `C` before dispatching `M`, persist `G`, idempotent handlers, exactly one poll in flight | 2 | P0 | M | SC-019 | Replayed duplicate/overlapping poll frames produce no duplicate events; crash between C-persist and dispatch recovers without loss |
| SC-021 | SignalR reconnect discipline: T:1 → /reconnect within 50 s window, D:1 → full restart, budget ≤ 1/20 s and ≤ 6/auction, park DOWN past budget, clean /abort on shutdown | 2 | P0 | M | SC-020 | Injected T:1 storm respects budget then parks; abort issued on SIGTERM (clean MessengerLogout in sim log) |
| SC-022 | ServerClock: ReceiveClock samples correlated with /send RTT (never the long poll), min-delay filter → θ ± ε, step-then-slew, monotonic base, 7-digit ISO parser in codec.py | 2 | P0 | L | SC-019 | On replayed recording, recovers the +13.15 s skew within ±0.1 s; poll-RTT poisoning test shows samples excluded; parser handles 7 fractional digits |
| SC-023 | Clock gates + degradation ladder: block bidding at ε > 2 s or age > 60 s; Date-header rung with ε floor 1.5 s; freeze-not-regress phase; HOLD + alarm | 2 | P0 | M | SC-022 | Property test: no BID decision reachable while gate closed; degraded rung keeps display honest but bidding blocked |
| SC-024 | StateHighWater regression guards: lifecycle rank ordering, end_epoch high-water, two-source corroboration to lower, sticky untradeable states, fail-closed on unknown strings | 2 | P0 | M | SC-018 | Replayed Started→ReadyToStart→Started flap never lowers the mark; `Canceled` (single l, any case) latches untradeable; unknown state = untradeable |
| SC-025 | Event-log transition authority: map Logs_Search_* rows to lifecycle overrides of stale REST reads | 2 | P0 | S | SC-024 | AuctionFinished row overrides a stale Started REST read in sim; all six known row types mapped |
| SC-026 | Politeness token bucket + phase cadences (IDLE 60 s / ARMED 10 s / ENDGAME 2.5 s / newData single-flight refetch with 300 ms debounce) | 2 | P0 | M | SC-018 | Global budget never exceeded under newData storm in sim; cadence transitions logged; debounce coalesces bursts |
| SC-027 | Watchdog OS thread: 250 ms heartbeat, 3 missed → all-channel alarm, > 5 s starvation → alarm + TOMAR CONTROL surface (never auto-kill) | 2 | P0 | S | SC-002 | Blocked-loop fault injection alarms at the right thresholds; kill switch untouched |
| SC-028 | Decision loop skeleton: single-writer CSP queue, typed events, shadow strategy, heartbeat, mode gates | 2 | P0 | M | SC-023, SC-024, SC-026 | Loop iteration ≤ 250 ms under fixture load; only the loop mutates AuctionState (enforced by review + convention test) |
| SC-029 | `centinela watch`: attend a real auction read-only, record every frame (REST, SignalR, clock samples) into the audit chain | 2 | P0 | M | SC-028, SC-021 | One full real auction captured with zero writes, zero politeness violations, replayable chain |
| SC-030 | Evidence mission: capture one human-placed manual bid end-to-end (browser HAR + our observer) to pin the SubmitBid contract — resolves OQ-02 | 2 | P0 | M | SC-029 | Raw request/response of a successful SubmitBid archived redacted under raw/; body schema diffed against ValidateBid; verb, path, and headers pinned |
| SC-031 | Evidence mission: observe ≥ 3 auctions with differing minimumBidType; tabulate δ base (start price vs last lance vs best) — resolves OQ-03 | 2 | P0 | M | SC-029 | Table of (auction, minimumBidType, minBidDifference, rejection strings, inferred rule) covering ≥ 2 distinct types; hypothesis graded per 02-auction-mechanics |
| SC-032 | Evidence mission: read-only negotiate probe for TryWebSockets/transport availability — resolves OQ-05 | 2 | P1 | S | SC-019 | negotiate response fields recorded; decision noted: longPolling remains default regardless (availability > speed) |
| SC-033 | Evidence mission: determine extension trigger (any qualifying bid vs winner-changing bid; semantics of activateExtraTimeWinner) — resolves OQ-04 | 2 | P0 | S | SC-029 | Observed extension events correlated with bid rank-impact across ≥ 2 auctions; rule written into 02-auction-mechanics with grade |
| SC-034 | vortal-sim: ASGI simulator speaking recorded REST + SignalR dialect, driven by Phase-0 fixtures | 3 | P0 | L | SC-011, SC-007 | Observer stack (SC-018..SC-028) runs unmodified against sim; golden SASI-001-2026 replay byte-faithful on wire shapes |
| SC-035 | Pathology injection in sim: backwards states, +13.15 s skew, T:1 storms, mid-auction 401, es-CO minBid strings, endDate mutation, disconnect storms | 3 | P0 | M | SC-034 | Each pathology has a scenario file + a test asserting the documented safe behavior |
| SC-036 | Adversary personas: probe-bidder (1,00/0,00 COP), aggressive stepper, endgame extender, stalemate | 3 | P0 | M | SC-034 | Personas scriptable per scenario; probe persona reproduces the captured 1,00/0,00 ladder |
| SC-037 | AuctionView projection: floor-clamped, probe-filtered ranks (serious-bid rule from 04-strategy-engine), pessimistic now, injected — strategy has no clock/floor-file access | 3 | P0 | M | SC-028, SC-008 | Probe bids excluded from my_rank/best_serious_visible; view is frozen; layer rules verified by import-linter |
| SC-038 | `minimal_step` strategy: smallest legal improvement while rank > 1 and ≥ floor; STOP + alarm at floor | 3 | P0 | M | SC-037 | Deterministic over golden replay; property: never bids at rank 1, never below floor, monotone descent |
| SC-039 | `patient_endgame` + `adaptive` strategies + mandatory bracket envelope per 04-strategy-engine | 3 | P1 | L | SC-038 | All pass the same invariant suite; bracket envelope clamps every strategy's output; tournament results archived |
| SC-040 | Hypothesis property suites: floor-safety, rank-1 abstention, determinism, event reorder/duplication invariance, es-CO parse fuzz | 3 | P0 | L | SC-038, SC-036 | ≥ 10⁵ cases per invariant in nightly CI; any counterexample fails the build with a reproducible seed |
| SC-041 | test_hotpath_latency.py: decide→guard→serialize ×10 000 on replay fixture, fail build if p99 > 5 ms; gc.freeze/disable harness for ENDGAME | 3 | P0 | S | SC-038, SC-043 | Gate enforced in CI on target hardware profile; measured numbers logged per run |
| SC-042 | `centinela copilot`: full read stack + guard + strategy, zero write capability, next-legal-bid clipboard copy | 3 | P0 | M | SC-038, SC-029 | Used live at a real auction; rendered bid always ≥ floor and legal per server rules; no write path importable from copilot module (lint-enforced) |
| SC-043 | guard/ package: L1 BidGuard + L2 type-level clamp making sub-floor totals unrepresentable; independently reviewed and version-pinned | 4 | P0 | L | SC-006, SC-008 | Separate review sign-off recorded; property test cannot construct a sub-floor ValidatedBid by any API; guard imports nothing from strategy |
| SC-044 | SubmitGateway: SetStartBiddingAudit → ValidateBid → driver → reconcile; asyncio.Lock across cycle; ValidatedBid TTL 5 s; byte-identical body reuse; ≤ 2 ValidateBid rejections/auction | 4 | P0 | L | SC-043, SC-030 | Sim tests: TTL expiry invalidates; third rejection latches gateway closed with alarm; validate and submit bodies byte-identical |
| SC-045 | L3 byte-level floor re-verification: re-parse the serialized outbound body and re-check every total against the FloorSheet immediately pre-send | 4 | P0 | M | SC-044 | Mutated-body fault injection (post-guard corruption) is caught at L3 and trips kill; check code shares nothing with L1/L2 implementations |
| SC-046 | Latched kill switch: file + signal + panel + Telegram sources, 4 Hz poll + pre-submit check, latch survives restart | 4 | P0 | M | SC-044 | Kill during in-flight cycle prevents send; latch persists across process restart; un-latch requires typed human confirmation |
| SC-047 | Browser submit driver (rung 2): prefill + submit via IPC; selector canary at ARM and ENDGAME entry; selectors.yaml + DOM build fingerprint | 4 | P0 | L | SC-013, SC-044 | Canary failure blocks ARM / downgrades to ASSISTED with alarm; selectors changed only via YAML; end-to-end sim-DOM submit passes |
| SC-048 | REST SubmitBid driver (rung 1) built from the OQ-02 capture, gated behind submit.confirmed=false until pinned against a real success | 4 | P0 | M | SC-030, SC-044 | Driver replays the captured bid byte-faithfully in sim; flag flip requires config change + typed confirmation; armed mode uses rung 2 while false |
| SC-049 | Reconciliation routine: on SignalR restart, ≥ 10 s unhealthy, process restart, every submit outcome, standby promotion → re-read GetAuction + GetSupplierBids + GetEventLogs, adopt server view, RECONCILE audit record | 4 | P0 | M | SC-044, SC-025 | All five triggers exercised in sim; strategy blocked until reconcile completes; server's "my last lance" always wins |
| SC-050 | AMBIGUOUS submit outcome handling: reconcile-then-decide, never blind retry; crash-recovery journal replay invalidates outstanding ValidatedBid | 4 | P0 | M | SC-049, SC-010 | Kill-mid-submit and crash-mid-submit sims recover to a consistent state with zero duplicate bids |
| SC-051 | L4 post-submit floor violation detection: compare server-confirmed lance to FloorSheet; violation → mandatory auto-kill + all-channel alarm | 4 | P0 | S | SC-049 | Injected sub-floor confirmation (sim) trips latched kill within one reconcile cycle |
| SC-052 | Mode ladder shadow→copilot→assisted→armed with typed confirmations; armed requires FloorSheet SHA acknowledgment by a named human | 4 | P0 | M | SC-046, SC-042 | Mode transitions audited with operator identity; no programmatic path to armed; Telegram can only lower the mode |
| SC-053 | Dry-run rehearsal: full armed cycle against live read path with stubbed drivers, then live kill drill | 4 | P0 | M | SC-052, SC-047 | One real auction shadowed in dry-run with decision log showing the exact bids it would have placed, all ≥ floor; kill drill passes |
| SC-054 | First armed live bid (Subasta de Prueba if OQ-13 allows, else lowest-stakes real auction): minimal_step, conservative floors, operator on TOMAR CONTROL | 4 | P0 | M | SC-053, SC-048/SC-047 | Bid validated then submitted above floor within budget; complete fsynced audit chain; post-auction report reviewed and archived |
| SC-055 | Latency/statistics store: SQLite WAL for per-endpoint latency and clock samples; feeds the 20 s/15 s cutoff arithmetic of 02-auction-mechanics | 4 | P1 | S | SC-018 | p50/p90/p99 per endpoint queryable; SKIP_NO_TIME decisions reference measured percentiles |
| SC-056 | Socrata clients + verified SoQL recipes (p6dx-8zbt, 9sue-ezhx, dmgg-8hin, hgi6-6wh3) with retry + app-token etiquette | 5 | P1 | M | SC-002 | All four recipes return live rows; lag (1–2 days) surfaced in output; no auction-day dependency |
| SC-057 | Qualification filter: UNSPSC V1.92121700 ∪ description keywords; SASI-type + status filters; dedup across datasets | 5 | P1 | M | SC-056 | Recall test against the known 2026 vigilancia SASI list from 06-tender-discovery; misclassified-entity case caught by keyword arm |
| SC-058 | Pliego fetch + margen mínimo extraction with confidence grading and human-review flag | 5 | P1 | L | SC-057 | Extracts the margin clause from ≥ 3 real pliegos; low-confidence extractions flagged, never silently trusted |
| SC-059 | Supervigilancia tariff engine (Layer-0 floor): current resolution ingested as versioned data, per-service-type post cost computation | 5 | P0 | L | SC-008 | Matches a hand-computed 24 h/30-day post to the centavo; resolution version pinned and displayed on every FloorSheet |
| SC-060 | AuctionCard/FloorSheet lifecycle: PipelineLead → AuctionCard → FloorSheet → PortalHandoff with mandatory human approval gate (D2) | 5 | P0 | M | SC-059, SC-058 | Unapproved FloorSheet cannot be loaded by the runner (signature absent); lifecycle invariants property-tested |
| SC-061 | `centinela scan` CLI + cron/launchd install; ranked card output; "do not enter" recommendation using rival-roster analytics | 5 | P1 | M | SC-057, SC-060 | Daily run emits ranked drafts; crowded-tender case yields an explicit do-not-enter with rationale |
| SC-062 | Operator panel: FastAPI + SSE, countdown on disciplined clock, both state machines, endDate vs originalEndDate, serious/probe leaderboard, leg health per rung, floor sheet SHA, next legal bid | 6 | P1 | L | SC-028, SC-042 | Renders from replay with zero build step; SSE survives leg restarts; localhost/WireGuard only |
| SC-063 | Panel controls: ARM/DISARM typed confirmation, latched KILL, TOMAR CONTROL focusing browser via noVNC ≤ 2 s | 6 | P0 | M | SC-062, SC-046, SC-013 | Takeover drill: human is clicking in the authenticated browser within 2 s; bot latches to shadow but keeps clock/state/alarms |
| SC-064 | Telegram notifier: T−30 readiness, ARMED, every bid, rank change, extension, degradation, SKIP_NO_TIME, kill, deadman (runner silent 10 s while ARMED); inbound = `/kill <auction>` only, shared-secret authed | 6 | P0 | M | SC-046 | Every event class delivered to a phone in a live drill; any inbound other than /kill ignored and logged; secret rotation documented |
| SC-065 | Post-auction report generator: replay audit chain → Spanish human-readable summary (bids, rank timeline, floors, margin retained vs floor KPI) | 6 | P1 | M | SC-010, SC-054 | Report for the first armed auction reviewed by management; KPI formulas match 00-context.md |
| SC-066 | Spanish runbooks: session bootstrap, auction-day checklist, kill drill, TOMAR CONTROL, incident response (five scenarios from 05-compliance.md) | 6 | P0 | M | SC-063 | A non-developer operator completes a supervised auction using only the runbooks; drill sign-off recorded |
| SC-067 | Compliance closeout: counsel sign-off on UNVERIFIED items from 05-compliance.md (Protocolo de Fallas location, art. 410A text, current tariff resolution, ToS automation posture) | 6 | P0 | M | — | Written counsel memo archived; any red flag converts to a blocking ticket before further armed runs |
| SC-068 | Marketplace abstraction: extract CO1-specific hosts/locale/currency/selectors/politeness into a marketplace profile; second profile loadable by vortal-sim | 7 | P2 | L | SC-054 | ruff/import-linter URL rules prove zero SECOP strings outside adapters/secop + profiles; sim runs under a synthetic second profile |
| SC-069 | Survey one non-CO1 Vortal/NextWay deployment (read-only, ToS reviewed first) + per-jurisdiction compliance annex template | 7 | P2 | L | SC-068, SC-067 | Written go/no-go with legal annex before any live contact; protocol deltas documented against 03-client-protocol.md |

---

## 4. Open questions register

| ID | Question | Why it blocks | Resolution path | Owner | Status |
|---|---|---|---|---|---|
| OQ-01 | Does the REST API accept the JWT alone, or does it require the browser session cookies (and which)? (= O1) | Determines whether the REST leg can outlive browser trouble; startup fails closed until known | SC-016 three-strategy self-test at first authenticated startup | Eng | **Open — Phase 1 exit gate** |
| OQ-02 | Exact SubmitBid contract: verb, path, headers, body schema, response, idempotency behavior (= O2) | Rung 1 (REST submit) stays gated `submit.confirmed:false`; armed mode limited to browser rung | SC-030: capture one real human-placed successful bid; pin byte-level; optionally ask Vortal/CCE per 03-client-protocol plan | Eng | **Open — blocks Phase 4 full exit** |
| OQ-03 | minimumBidType semantics: what is the base of the minimum decrement (start price vs last valid lance vs current best), per type value? (= O-M1) | Wrong δ wastes the 2-rejection ValidateBid budget or bids illegally high steps; SASI-001-2026 (1% of start, observed) vs the UNP SASI-064-2026 pliego (2% of last lance, documentary) already contradict | Read `GetAuction.minBidDifference` per auction; SC-031 multi-auction observation; ≤ 2 live ValidateBid calibrations per auction as last resort | Eng | **Open — architecture already mandates server-resolved δ** |
| OQ-04 | Does extra time trigger on ANY qualifying last-minute bid or only a winner-changing one (what exactly does `activateExtraTimeWinner:true` gate)? | Endgame timing model and the T−45 s policy depend on it; affects whether rivals can extend indefinitely without improving | SC-033 correlation of extension events with bid rank-impact across ≥ 2 observed auctions; event-log rows are ground truth | Eng | Open |
| OQ-05 | Is webSockets (or serverSentEvents) transport available on /signalr, or only longPolling? | Minor latency/robustness optimisation; longPolling is the committed default either way | SC-032 read-only negotiate probe (`TryWebSockets` field) | Eng | Open — low stakes by design |
| OQ-06 | Platform ToS position on automation: the analyzed ToS is silent — does CCE/Vortal have any explicit policy, and does the Protocolo de Fallas (linked 404) impose duties? | Legal exposure of the entire project; Decreto 1082 makes a blocked connection "imputable al oferente" | SC-067: Colombian counsel memo; formal written query to CCE/Vortal considered per 05-compliance.md | Legal | **Open — counsel sign-off required before sustained armed operation** |
| OQ-07 | Exact floor-price cost model for STARCOOP: mapping pliego staffing schedules to 24 h/30-day-equivalent posts under the current tariff resolution (= D2) | Largest floor-price error source; a wrong FloorSheet defeats all five guard layers from above | SC-059 tariff engine + SC-060 mandatory human FloorSheet approval; back-test against past awarded contracts | Ops + Legal | **Open — human gate is permanent until proven** |
| OQ-08 | Are redundant bidders safe? (Two sessions / two runners / bot + human on the same account simultaneously — does the platform reject, duplicate, or corrupt?) | Determines the HA story and the TOMAR CONTROL handoff rules; a duplicate lance cannot be retracted | Observe session semantics in Subasta de Prueba (OQ-13); until then the (auctionId, companyCode) lockfile + human-takeover-latches-bot-to-shadow rule stand | Eng | Open — mitigated by design |
| OQ-09 | Silent token refresh: does re-navigating the auction room mint a fresh JWT without reCAPTCHA while cookies live? (= O11) | Determines whether a > 24 h auction day needs a second human login | SC-015 refresh instrumentation during Phase-2 observation | Eng | Open |
| OQ-10 | GetSupplierBids pagination contract: page size, ordering, termination condition (= O12) | Leaderboard truth on crowded auctions (44 rivals observed) | SC-018 paginate-until-empty against a crowded live auction; fixture added | Eng | Open |
| OQ-11 | Does the improvement constraint bind against one's OWN last lance or the global best, and what does `allowBidsBellowBestBid:false` actually gate? (= O-SE1) | Decides whether the game is a public price war or parallel hidden descent; shapes every strategy | SC-031/SC-033 observation + the R4 analysis in 02-auction-mechanics; SubmitBid capture (OQ-02) is corroborating evidence | Eng | Open |
| OQ-12 | Byte-level SignalR details: HTTP verb per endpoint and `messageId` placement (POST body vs query) on /poll and /reconnect | Hand-rolled client correctness; flagged as first implementation task in 03-client-protocol.md | SC-019: byte-level diff against raw HARs before any live SignalR use | Eng | Open — resolvable offline |
| OQ-13 | Subasta de Prueba: how does a supplier get access to a test auction, and does it exercise the real bid path? | Safest venue for SC-054 first armed bid and OQ-08 experiments | Ask CCE support / mesa de ayuda; check portal for test-tender listings | Ops | Open |

---

## 5. Decision log

| ID | Date | Decision | Rationale | Status |
|---|---|---|---|---|
| DL-01 | 2026-07-28 | Python 3.12, single asyncio process per auction, `mypy --strict`, uvloop | Platform latency (p50 ~900 ms) dwarfs runtime overhead; hand-rolled SignalR needed in any language; evidence tooling already Python; enforced no-float lint beats opt-in decimal discipline in Node | Accepted |
| DL-02 | 2026-07-28 | Browser-assisted (human) auth bootstrap; no headless CAPTCHA evasion, ever | Login is reCAPTCHA-gated by design; evasion would be both unreliable and a ToS/legitimacy breach; 24 h JWT makes one human login per day acceptable | Accepted |
| DL-03 | 2026-07-28 | ValidateBid always precedes SubmitBid; byte-identical body reuse; ≤ 2 ValidateBid rejections per auction | Hard constraint 4; failures are permanently logged against our NIT and IP in the entity's audit log (2/2 observed) | Accepted |
| DL-04 | 2026-07-28 | Five-layer floor enforcement (L0 signed FloorSheet → L4 post-submit detection) with latched auto-kill on violation; sub-floor bids made type-unrepresentable | One wrong lance is unrecoverable and, below the Supervigilancia tariff, illegal — floor is law in both senses | Accepted |
| DL-05 | 2026-07-28 | Read-only observer first; co-pilot (zero-write) is the first shipped product; armed bidding last | Resolves OQ-02/OQ-03 with zero write risk; builds operator trust; exercises clock/state/guard live before any driver exists | Accepted |
| DL-06 | 2026-07-28 | Secrets never committed; keychain/Docker secrets; JWT-redacting log processor; HAR-leaked credentials rotated before any further work | Terms-of-Use signature semantics (Ley 527/1999) make the credential the company's legal signature; the evidence corpus itself caused a leak | Accepted |
| DL-07 | 2026-07-28 | Hand-rolled SignalR client (~600 lines over httpx) instead of any packaged client | No maintained client exposes cursor/`G`/token-query-param/reconnect knobs; wire-level control is non-negotiable | Accepted |
| DL-08 | 2026-07-28 | Minimum decrement δ always read from server (`minBidDifference` + `minimumBidType`), never computed from the 1%-of-start hypothesis | A documented counter-configuration exists (UNP SASI-064-2026 pliego: 2% of last valid lance); the server is the only rule oracle | Accepted |
| DL-09 | 2026-07-28 | Bid early (target T−45 s), never snipe | Unlimited auto-extensions (any last-minute qualifying bid +3 min) mechanically abolish sniping; availability beats speed | Accepted |
| DL-10 | 2026-07-28 | Playwright browser runs as a separate OS process behind JSON-IPC | A wedged browser must never stall the decision loop, REST leg, or clock (shared-fate flaw fix) | Accepted |
| DL-11 | 2026-07-28 | Watchdog starvation alarms and surfaces TOMAR CONTROL; it does NOT trip the latched kill (floor violations excepted) | Auto-kill on transient OS pause converts a survivable stall into a self-inflicted withdrawal | Accepted |
| DL-12 | 2026-07-28 | Single stable attributable egress IP + global politeness budget as correctness requirements | WAF blocks "consultas masivas"; a blocked IP = connection loss "imputable al oferente" = deemed withdrawal (Decreto 1082 art. 2.2.1.2.1.2.6) | Accepted |
| DL-13 | 2026-07-28 | REST SubmitBid rung gated behind `submit.confirmed:false` until pinned from a real captured successful bid; armed mode uses the browser rung meanwhile | The SubmitBid contract was never captured; guessing a write endpoint on a state platform is unacceptable | Accepted |
| DL-14 | 2026-07-28 | Telegram inbound limited to `/kill <auction>`; remote control can only ever make the bot less active | Compromised chat channel must not be able to place bids or arm the system | Accepted |
| DL-15 | 2026-07-28 | Multi-daemon/automated-HA topology deleted; one binary, one process per auction, one YAML, cron-invoked discovery | Operable by a security cooperative, not an SRE team (operational-weight flaw fix) | Accepted |
| DL-16 | 2026-07-28 | CI never touches `*.secop.gov.co`; all platform pathologies reproduced in the in-repo vortal-sim | Principle 10: what is not tested offline does not exist; also politeness — the platform is not our test bench | Accepted |

---

## 6. Next three actions

1. **Rotate the credentials (SC-001), today.** The HAR corpus contains a live-lifetime JWT and session cookies for the account whose credentials are legally STARCOOP's electronic signature. Change the SECOP account password, confirm the old token is dead, write the incident note. Nothing else authenticated happens first.
2. **Stand up the safety skeleton (SC-002 → SC-011).** Repo, CI with the no-network guard, layer contracts, the no-float lint, the `Centavos` codec, the typed API contract validated against the HARs, and the versioned replay fixtures. This is roughly two weeks of work that makes every subsequent line of code checkable, and it needs no SECOP access at all.
3. **Get to the next real auction as an observer (SC-013 → SC-033).** Build the session bootstrap and the read-only stack, then attend STARCOOP's next SASI with `centinela watch` running and a human placing one deliberate manual bid while we record it. That single session resolves or advances OQ-01, OQ-02, OQ-03, OQ-04, OQ-09 and OQ-10 — the entire critical path of unknowns — without the bot ever writing a byte.
