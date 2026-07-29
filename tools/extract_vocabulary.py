#!/usr/bin/env python3
"""Extract the SECOP II auction platform vocabulary from HAR captures.

The auction site fetches a localization bundle (`GetResourceLocaleString`) containing
every UI string, including the full bid-validation error taxonomy. Those keys are what
the API returns in `errorMessageLocale`, so they are the most reliable enumeration of
error conditions available without triggering each one against the live platform.

Usage:
    python3 tools/extract_vocabulary.py <har-directory> <output.md>
"""
import json
import glob
import os
import sys

GROUPS = [
    ('Bid validation errors', (
        'These are the failures `ValidateBid` and `SubmitBid` can return. The API response '
        'field `errorMessageLocale` maps directly to these keys, so the bot should branch on '
        'the **key**, never on the Spanish prose (which is localised and may change).'
    ), ('BidWarn', 'BidErr')),
    ('Bid and price-list vocabulary', '', ('Bids', 'Bid_')),
    ('Auction status and connection status', (
        'The auction room surfaces these. `ConnectionStatus_*` are what the screen recording '
        'displayed as `ESTADO: DESCONECTADO` when the SignalR socket dropped mid-auction.'
    ), ('Status', 'ConnectionStatus', 'Sidebar')),
    ('Event log vocabulary', (
        '`GET /v1/audit/GetEventLogs` returns audit events described by these keys. They are '
        'the primary means of reconciling what happened while the bot was disconnected.'
    ), ('Log', 'Logs', 'Events')),
    ('Auction settings vocabulary', (
        'The configuration surface of an auction. Mirrors the fields returned by '
        '`GET /v1/auction/GetAuctionSimple/{auctionId}`.'
    ), ('Settings',)),
    ('Chat and participants', '', ('Chat', 'ChatHistory')),
]

ANALYSIS = """
## What these strings reveal about the rules

Confidence markers: **CONFIRMED** = the platform served this string. **DERIVED** = our
reading of what it implies. Interpretations below are DERIVED and must be verified
against a live auction before the bot relies on them.

### 1. Validation happens at three independent scopes — DERIVED

The error keys come in three parallel families:

| Scope | Key family | Meaning |
|---|---|---|
| Item | `BidWarn_Desc`, `BidWarn_Asc` | Each individual item's bid must improve on your last bid for that item |
| Lot | `BidWarn_Lot_Value_*`, `BidWarn_Lot_Min_Bid_Diff_*` | Each lot's aggregate must improve, respecting that lot's minimum step |
| Total | `BidWarn_Total_Value_*`, `BidWarn_Total_Min_Bid_Diff_*` | The auction-wide total must improve, respecting the total minimum step |

**Consequence for implementation:** in a multi-lot auction a bid is legal only if it
satisfies *all three* scopes simultaneously. Computing a bid that improves the total
while accidentally violating a single item's constraint produces a rejected bid — and a
rejected bid inside the final minute is a wasted opportunity that cannot be retried
before the clock runs out. The strategy engine must solve the constraint set, not just
subtract a step from the total.

### 2. The first bid is a special case — DERIVED

`BidWarn_Min_Bid_Diff_Desc` states: *"Si es su primer lance, este tiene que ser igual al
precio base del lance. Si no es su primer lance, este tiene que ser inferior a su último
lance y debe respetar el lance mínimo definido."*

Read literally, in a descending auction **your first bid must equal the base price**, and
only subsequent bids move downward by at least the minimum step. If true, the bot must
open at the base price rather than jumping straight to its target — meaning it needs at
least two round trips to reach a competitive price, which changes the timing budget.

The ascending variant (`BidWarn_Min_Bid_Diff_Asc`) is more permissive: *"puede ser igual
al precio base ... o puede aplicar el lance mínimo definido"*. The asymmetry is
suspicious and this rule is a **priority open question**.

### 3. The minimum-step wording is self-contradictory — CONFIRMED quirk

Several strings read *"tiene que ser inferior al último lance **más** la diferencia
mínima total"* — literally "must be lower than the last bid **plus** the minimum
difference". For a descending auction the arithmetic that actually makes sense is:

```
newTotal <= lastTotal - minimumDifference
```

The word *más* appears to be a translation defect in the platform, present in both the
ascending and descending variants. **Do not implement from the prose.** Implement from
`ValidateBid`'s accept/reject behaviour, which is authoritative.

### 4. There is an explicit "auction not running" guard — CONFIRMED

`BidWarn_Auction_Not_In_Progress` ("La subasta ya no está en curso") confirms the server
rejects bids outside the running window. The bot must therefore treat its own clock
estimate as advisory and be ready to handle this rejection gracefully rather than
assuming a bid submitted before its computed deadline will be accepted.

### 5. Decimal precision is enforced — CONFIRMED

`BidWarn_Wrong_Decimal` ("El número de dígitos soportado ha sido ultrapasado") confirms
the `decimalNum` / `totalNum` fields from `GetAuctionSimple` are enforced server-side.
Bid values must be rounded to the auction's declared precision before submission, using
the auction's `roundTypeCode`.
"""


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    har_dir, out_path = sys.argv[1], sys.argv[2]

    loc = None
    for f in sorted(glob.glob(os.path.join(har_dir, '*.har'))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        for e in d['log']['entries']:
            if 'GetResourceLocaleString' in e['request']['url']:
                txt = e['response'].get('content', {}).get('text')
                if txt:
                    loc = json.loads(txt)
                    break
        if loc:
            break

    if not loc:
        print('No localization bundle found in HARs', file=sys.stderr)
        return 1

    out = [
        '# SECOP II e-Auction — Platform Vocabulary',
        '',
        '*The platform\'s own string table, extracted from the auction site\'s localization '
        'bundle. It is the most reliable enumeration of auction states and error conditions '
        'we can obtain without triggering each one against the live system.*',
        '',
        'Source: `GET /eauctions.api/v1/localization/GetResourceLocaleString'
        '?auctionId=..&languageCode=es-CO&marketCode=..`',
        '',
        f'Bundle size: **{len(loc)} keys**, locale `es-CO`.',
        '',
        'Regenerate with `python3 tools/extract_vocabulary.py <har-dir> '
        'docs/09-platform-vocabulary.md`.',
        '',
        'See also: [API contract](01-api-contract.md), '
        '[auction mechanics](02-auction-mechanics.md).',
        '',
        ANALYSIS.strip(),
        '',
        '---',
        '',
        '# Reference tables',
        '',
    ]

    used = set()
    for title, note, prefixes in GROUPS:
        keys = sorted(k for k in loc if k.startswith(prefixes) and k not in used)
        if not keys:
            continue
        used.update(keys)
        out += [f'## {title}', '']
        if note:
            out += [note, '']
        out += ['| Key | Spanish string |', '|---|---|']
        for k in keys:
            v = str(loc[k]).replace('|', '\\|').replace('\n', ' ')
            out.append(f'| `{k}` | {v} |')
        out.append('')

    rest = sorted(k for k in loc if k not in used)
    out += [
        '## Omitted',
        '',
        f'{len(rest)} further keys cover generic UI chrome, admin-only screens and export '
        'labels with no bearing on bot behaviour. Regenerate this file to inspect them.',
        '',
    ]

    open(out_path, 'w', encoding='utf-8').write('\n'.join(out))
    print(f'wrote {out_path}: {len(used)} keys documented, {len(rest)} omitted')
    return 0


sys.exit(main())
