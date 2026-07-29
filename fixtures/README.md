# Fixtures

Replayable auction data derived from real captures. These exist so the strategy engine
and the auction runtime can be tested **without touching the live SECOP II platform** —
which matters both because auctions are rare events we cannot summon on demand, and
because probing a government system to generate test data is not acceptable.

## `auction-sasi-001-2026.json`

A real *subasta inversa* captured on 2026-03-02.

| | |
|---|---|
| Tender | `SASI-001-2026` |
| Buyer | Instituto Distrital de Protección y Bienestar Animal |
| Item | MEDIOS TECNOLÓGICOS (1 PS) |
| Start price | 115 670 523 COP |
| Minimum step | 1.0% of initial price = 1 156 705,23 COP |
| Scheduled window | 10 minutes, with 3-minute auto-extensions |
| Registered bidders | 48 |
| Events | 363, spanning ~583 s |

### Shape

```jsonc
{
  "$schema": "starcoop/auction-replay/v1",
  "auction": { /* rules: type, step, extra time, lots and items */ },
  "stats":   { /* event counts, competitor count */ },
  "events": [
    { "offsetMs": 0, "at": "...", "type": "clock.tick", "serverTime": "..." },
    { "offsetMs": 321104, "type": "self.validate_bid", "request": {...}, "response": {...} },
    { "offsetMs": ..., "type": "bidbook.snapshot", "bids": [...] },
    { "offsetMs": ..., "type": "auction.new_data", "flags": [true, false, false] },
    { "offsetMs": ..., "type": "participants.onUserDisconnected", "participants": [...] },
    { "offsetMs": ..., "type": "audit.entry", "eventSubType": "...", "description": "..." }
  ]
}
```

`offsetMs` is milliseconds from the first event, so a replay harness can either run in
real time or fast-forward deterministically.

### Event types

| Type | Meaning |
|---|---|
| `clock.tick` | Authoritative server time from `auction.ReceiveClock` |
| `auction.new_data` | The platform's "something changed, refetch" signal |
| `bidbook.snapshot` | A `GetSupplierBids` response — the observable bid book and rankings |
| `self.validate_bid` | Our own `ValidateBid` attempt and the platform's verdict |
| `participants.*` | Bidders connecting, reconnecting and dropping |
| `audit.entry` | Rows from the platform's permanent event log |

### What this fixture is good for

- **Reconnection testing.** 36 of the 44 companies dropped at least once, 100 disconnect
  and 199 reconnect events in ten minutes. This is a realistic hostile network trace.
- **Validation-rule testing.** It contains two real rejected bids with the platform's
  exact `errorType`, `errorMessageLocale` and quoted `minBid`, so the bid-arithmetic
  implementation can be checked against ground truth.
- **Clock-sync testing.** 41 real server clock ticks with their observed arrival times.
- **Strategy smoke-testing.** Real competitor bid values and timings, including the
  probe bids of 1.00 and 0.00 COP that a naive strategy would chase below its floor.

### What it is *not*

It does **not** contain a successful `SubmitBid`, because neither captured bid attempt
passed validation. Resolving that contract requires the read-only observer phase against
a live auction. See the open-questions register in [`../docs/08-roadmap.md`](../docs/08-roadmap.md).

## Privacy and safety

Competitor company codes are replaced with stable pseudonyms (`COMPETITOR_01`, …) and
our own account appears as `SELF`. Company names, NITs, contacts, emails, IP addresses
and JWTs are redacted. The generator aborts if a JWT survives into the output.

This is not cosmetic. The platform anonymises rivals deliberately, and decision **D-09**
in [`../SOT.md`](../SOT.md) forbids resolving a `companyCode` to an identity. The fixture
is built so that it cannot be used to profile named competitors even by accident.

## Regenerating

```bash
python3 tools/build_replay_fixture.py /path/to/har-directory \
        fixtures/auction-sasi-001-2026.json
```

The raw HAR captures are **not** in this repository — they contain live session
credentials and are blocked by `.gitignore`.
