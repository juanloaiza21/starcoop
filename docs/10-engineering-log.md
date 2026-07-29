# Engineering Log and Retrospective

*What was built, how, what we got wrong, and — the part worth your attention — where this
should improve next. Written 2026-07-29, covering the work from the initial evidence
bundle through the first working offline build.*

---

## 1. Where the project stands

We started with a 683 MB zip of unlabelled network captures and a screen recording. We
now have a documented, reverse-engineered understanding of the SECOP II auction platform
and a tested offline implementation of the decision core.

| | |
|---|---|
| Commits | 27, all pushed to `main` |
| Documentation | 6 464 lines across 12 chapters |
| Source | 1 751 lines across 11 modules |
| Tests | **152**, all passing; `ruff` and `mypy --strict` clean |
| Can it bid? | **No — deliberately.** Nothing in the repo can reach the live platform |

That last row is the important one. The read-only observer phase has not happened yet,
the `SubmitBid` contract is still unknown, and no code exists that could send a bid even
by accident. That is the intended state, not an unfinished one.

---

## 2. What was built

### Documentation

| Chapter | What it covers |
|---|---|
| [`SOT.md`](../SOT.md) | Source of Truth: decisions, the ten load-bearing facts, open questions |
| [`00-context`](00-context.md) | Business context, SECOP II, the captured auction, KPIs |
| [`01-api-contract`](01-api-contract.md) | Reverse-engineered API — **generated** from the captures |
| [`02-auction-mechanics`](02-auction-mechanics.md) | Rules engine: decrements, extra time, state machines |
| [`03-client-protocol`](03-client-protocol.md) | SignalR, clock sync, reconnection, authentication |
| [`04-strategy-engine`](04-strategy-engine.md) | What to bid, when, and when to stop |
| [`05-compliance`](05-compliance.md) | Colombian law, platform ToS, guardrails |
| [`06-tender-discovery`](06-tender-discovery.md) | Finding and qualifying auctions worth entering |
| [`07-architecture`](07-architecture.md) | The decided system design |
| [`08-roadmap`](08-roadmap.md) | Phases, 69-ticket backlog, open questions, decision log |
| [`09-platform-vocabulary`](09-platform-vocabulary.md) | The platform's own 566-key string table |
| [`REVIEW-2026-07-28`](REVIEW-2026-07-28.md) | Adversarial review: 32 findings, what was fixed |

Four chapters are **generated from evidence rather than written**, so they can be
regenerated when a fresh capture arrives: the API contract, the platform vocabulary, the
replay fixture, and the timing analysis. The tools live in `tools/`.

### Code

| Module | Lines | Tests | Responsibility |
|---|---:|---:|---|
| `domain/money` | 87 | 11 | Integer centavos; parses both platform money dialects |
| `domain/rules` | 209 | 23 | Bid legality, floor guard, next legal bid |
| `domain/clock` | 156 | 13 | Server-time estimation with round-trip compensation |
| `domain/latency` | 116 | 12 | Live submit-cycle budget |
| `domain/auction` | 209 | 14 | Ruleset resolution; fail-closed on unknown shapes |
| `domain/replay` | 182 | 10 | Offline replay of a captured auction |
| `transport/signalr` | 130 | 18 | Classic-SignalR wire format and cursor discipline |
| `strategy/engine` | 165 | 21 | Hold / bid / stop / skip decisions |
| `config/floor` | 209 | 13 | The floor sheet as a controlled document |
| `simulation` | 175 | 8 | Dress rehearsal over the captured auction |
| `cli` | 113 | — | `check-floor`, `replay` |
| *(purity guard)* | — | 9 | Fails the build if a float enters the money path |

---

## 3. Method

Three things shaped how this was done, and each is worth keeping.

**Mechanical extraction, human reasoning.** The HARs are 80 MB of JSON. Rather than
reading them, I wrote tools that distil them into a 39 KB contract with credentials
redacted at generation time. Analysis then ran over the digest. This is why the contract
chapter can be regenerated rather than maintained by hand, and why no JWT ever reached
git.

**Multi-agent research, then adversarial review.** Five parallel research agents covered
mechanics, protocol, legal, discovery and strategy. Three independent architectures were
proposed and scored by a three-lens judge panel, which chose the reliability-first design
unanimously. A critic agent then attacked the result and found 32 issues. That critic
earned its cost — see §5.

**Test-first, against real data.** Every module was written by watching a test fail
first. The most valuable tests are driven by the captured auction rather than by
scenarios I invented: the replay reproduces the ground truth independently (44
participants, 100 disconnects, 199 reconnects, both platform rejections), and the SignalR
parser is pinned against frames copied verbatim from the wire.

---

## 4. What we learned that changed the design

Four findings each inverted an assumption that seemed obvious at the start.

**Speed is not the edge.** Measured latency: `signalr/send` p50 832 ms, REST p90 6.9 s,
max 10.1 s. A submit is two sequential round trips, so p90 ≈ 14 s. Milliseconds of local
optimisation are irrelevant. What *is* relevant: 36 of the 44 connected companies dropped
at least once in ten minutes. Staying connected beats reacting fast.

**Sniping is the wrong goal.** A bid inside the last minute extends the auction by three
minutes, handing every rival another chance. The engine therefore fires *before* the
closing window so the auction can end with us in front.

**Validation is not free.** A rejected `ValidateBid` writes a permanent, entity-visible
audit row. The bot must therefore refuse illegal bids locally before spending an attempt.

**The minimum step never shrinks.** `minimumBidType` is `Percentage` of the *initial*
price, making it a fixed absolute amount for the whole auction. An implementation
recomputing 1 % against the *current* price drifts downward and starts producing bids
that look right and get rejected. This is encoded as conformance vector V-006 precisely
because it is the most plausible silent bug in the system.

---

## 5. What we got wrong, and how it surfaced

Recording these because the *mechanism* that caught each one is worth keeping.

| Error | Caught by |
|---|---|
| Claimed "validation is free and leaves no trace" — the opposite of what the audit log shows | Adversarial critic; I then verified against raw captures |
| Asserted `companyCode` is the NIT. It is a Vortal supplier code; the NIT is a different number | Research agent querying live open data |
| A chapter graded a *pliego clause* as an *observed API rejection*, for a tender that had not yet run | Critic cross-referencing two chapters |
| Two chapters adopted opposite endgame timing doctrines (fire inside vs outside the extension window) | Three verifiers independently |
| Rounding the emitted bid **up**, which lifts it above the legal threshold | Three verifiers independently |
| Described five floor layers as "independent" when three share a failure point | Critic |
| **A test that passed vacuously** — "never proposes below floor" was true only because it proposed nothing at all | Me, introspecting the values behind a green test |
| Two tests encoding my own arithmetic errors about clamping to the floor | The tests failed against correct code; I fixed the tests |

The last two are the ones I would emphasise to anyone continuing this work. A green test
is not evidence until you have looked at what it actually asserted.

---

## 6. Where we could improve

This is the section to read if you are picking the project up.

### 6.1 A real defect in what is already built

**The clock estimator can lock onto a stale offset and never update.**
`ServerClock.observe` adopts a new sample only when it is *tighter* than the one held. If
an early sample happens to have a very low round-trip time, no later sample is ever
adopted, so genuine clock drift is never corrected. Demonstrated:

```
tight early sample  → offset 0.450s, uncertainty 0.050s
much later, drifted → sample REJECTED; offset still 0.450s
```

The failure is fail-safe rather than dangerous — `is_fresh()` goes false and the engine
refuses to act — but the practical effect is a self-inflicted outage: one lucky fast
first sample and the bot may be unable to act for the rest of the auction. **Fix:** age
samples out, keeping the tightest within a sliding window (say 60 s) rather than the
tightest ever, and re-estimate continuously. This should be done before any live use.

### 6.2 The largest missing pieces

Roughly in order of what blocks progress:

1. **The SignalR *connection loop*.** Only the wire format exists. The stateful part —
   `negotiate → connect → start → poll`, threading the cursor across reconnects,
   backoff, honouring `T:1`, reconciling missed messages against `GetSupplierBids` — is
   the genuinely hard component and is unwritten. Everything live depends on it.
2. **Authentication bootstrap.** Login is reCAPTCHA-gated, so this needs the
   Playwright-assisted flow with persisted storage state. Also unresolved: whether REST
   truly needs the full cookie jar (§6.4).
3. **The submit gateway.** Cannot be written until `SubmitBid` is observed.
4. **Audit log.** The architecture calls for append-only hash-chained JSONL with `fsync`
   on the bid path. Nothing exists. This is a compliance requirement, not a nicety.
5. **Kill switch and operator UI.** Decision D-08 promises a human can take control
   instantly. Today there is no mechanism to take control of.
6. **Tender discovery.** Chapter 06 designs the pipeline; none of it is built.

### 6.3 Weaknesses in what *is* built

- **Multi-lot is only half-done.** `AuctionRuleset` parses multiple lots with independent
  steps, but `BidRules` and the strategy engine are scalar — one reference, one step, one
  floor. The platform validates at *three* scopes simultaneously (item, lot, total), and
  we implement one. Until this is closed, the "works universally everywhere" requirement
  is not met for multi-lot auctions.
- **Extensions are read but not modelled.** The ruleset knows the extension window and
  period; the engine never moves `end_at` when an extension fires. In a live auction the
  clock would silently be wrong after the first extension.
- **`replay.py` and `simulation.py` overlap.** Both parse the fixture and recompute the
  minimum step. They should share one loader; the duplication is exactly where the two
  will drift apart.
- **The latency budget is not wired to anything.** `record_validate` / `record_submit`
  are never called by real code, and the simulation feeds a hardcoded 1.0 s.
- **Two dev dependencies are unused.** `hypothesis` and `respx` are installed but no
  property-based tests or mocked-HTTP tests exist. The bid arithmetic in particular is an
  ideal target for property testing: *no legal bid is ever below the floor* and *every
  proposal is exactly one step below the reference* are properties, not examples.

### 6.4 Unknowns that could still invalidate work

| Unknown | Why it matters |
|---|---|
| The `SubmitBid` contract | No bid can be placed. Blocks everything live |
| Whether the first bid must equal the base price | Costs an extra round trip; changes the timing budget |
| Exact REST authentication | We *derived* that a cookie jar is needed from 2 528 bytes of stripped headers. If wrong, the session model changes |
| Other `minimumBidType` values | We fail closed on them, so unknown auctions are refused rather than mis-bid |
| Whether extra time triggers on any bid or only a winner-changing one | Changes endgame strategy materially |
| **The platform's position on automation** | Genuinely unresolved. This could invalidate the project entirely, and no amount of engineering settles it |

That last row deserves emphasis: it is a business and legal question, not a technical
one, and it should be resolved before significant further investment.

### 6.5 Process improvements

- **Give parallel authoring agents a shared decision ledger.** The contradictions in §5
  arose because agents wrote chapters concurrently with no binding record of what was
  already settled. Chapters sharing a parameter should either be written serially or be
  handed the decisions table as a hard constraint.
- **Require a quoted source for any `CONFIRMED` claim.** The fabricated evidence grade
  would not have survived a rule that says a confirmed claim must cite the line it came
  from.
- **Introspect green tests.** Both hollow tests I wrote looked fine and passed. Printing
  the actual values took thirty seconds and caught both.
- **The critic was worth more than any single author.** If the budget for this work were
  halved, I would cut an authoring agent before cutting the adversarial review.

### 6.6 A strategic reconsideration

Everything built so far is *execution* — how to bid well once you are in the room. The
evidence argues the bigger lever is upstream. The captured auction had **44 connected
competitors** for one contract, and a rival bid 0,00 COP. In a room that size someone
will bid to their own floor, and the winner takes a margin nobody wants.

The bot's most valuable output on that auction was refusing to play. That suggests
**tender discovery and qualification (chapter 06) may have higher return than further
execution work**, and that the roadmap's ordering — which puts discovery at Phase 5 —
deserves to be revisited rather than followed on autopilot.

---

## 7. Recommended next steps

1. **Rotate the SECOP II password for `Starcoopcta2017`.** The evidence bundle contained
   a live JWT and session cookies. Nothing else should happen first.
2. **Settle the ToS question** (§6.4). It is cheap relative to everything downstream and
   it can invalidate all of it.
3. **Fix the clock estimator** (§6.1). Small, self-contained, and a prerequisite for
   trusting any live timing.
4. **Build the read-only observer.** Connection loop plus logging, submitting nothing.
   This is what resolves `SubmitBid`, the first-bid rule, and the REST auth question at
   once — and it retires more unknowns per unit of work than anything else available.
5. **Close the multi-lot gap** before claiming universality.

---

## 8. Honest assessment

The plan is unusually well-evidenced for its stage: the rules are read from the
platform's own payloads, the timing figures are measured rather than assumed, and every
claim carries a confidence marker that survived an adversarial pass. The offline core is
small, tested, and safe by construction — money cannot be a float, and a bid below the
floor cannot be produced.

What does not exist is everything that touches the network. The hardest remaining
component, the reconnecting SignalR loop, is also the one the whole thesis rests on,
since the edge we identified is *staying connected while rivals drop*. That component has
not been started, and no amount of offline testing will substitute for watching one real
auction end to end.

Treat the current state as a well-founded starting position, not a nearly-finished
product.
