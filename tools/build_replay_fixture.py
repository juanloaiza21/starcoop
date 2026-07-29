#!/usr/bin/env python3
"""Build a replayable auction fixture from the HAR captures.

The captures contain a real SECOP II reverse auction. This tool flattens them into a
single ordered event stream so the strategy engine can be exercised offline, against
real timings and real competitor behaviour, without touching the live platform.

The fixture is deliberately transport-agnostic: it describes *what happened and when*,
not how it arrived over SignalR. A replay harness feeds these events to the engine and
asserts on the decisions it produces.

Credentials, IP addresses and competitor identities are redacted. Competitor company
codes are replaced with stable pseudonyms (`COMPETITOR_01`, ...) so the fixture cannot
be used to profile named rivals — see decision D-09 in SOT.md.

Usage:
    python3 tools/build_replay_fixture.py <har-directory> <output.json>
"""
import json
import glob
import hashlib
import os
import re
import sys
from datetime import datetime
from urllib.parse import unquote, urlparse

JWT_RE = re.compile(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}')
IPV4_RE = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')

# The account the captures belong to. Kept in the clear because the fixture is
# STARCOOP's own record of its own auction; everyone else gets a pseudonym.
OWN_COMPANY = '700961121'


class Pseudonymiser:
    """Stable, non-reversible pseudonyms for competitor company codes."""

    def __init__(self):
        self._map = {}

    def __call__(self, code):
        if code is None:
            return None
        code = str(code)
        if code == OWN_COMPANY:
            return 'SELF'
        if code not in self._map:
            self._map[code] = f'COMPETITOR_{len(self._map) + 1:02d}'
        return self._map[code]

    @property
    def count(self):
        return len(self._map)


def ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00'))


def scrub(obj, pseudo):
    """Recursively redact secrets and pseudonymise company codes."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in ('companyCode', 'company', 'userCode', 'internalId') and isinstance(v, (str, int)):
                sv = str(v)
                # internalId is only a company code when it looks like one
                if k != 'internalId' or re.fullmatch(r'\d{9,}', sv):
                    out[k] = pseudo(sv)
                    continue
            if k in ('ipAddress',):
                out[k] = '<IP_REDACTED>'
                continue
            if k in ('companyName', 'alias', 'companyNIF', 'companyContact', 'companyEmail'):
                out[k] = '<REDACTED>'
                continue
            out[k] = scrub(v, pseudo)
        return out
    if isinstance(obj, list):
        return [scrub(v, pseudo) for v in obj]
    if isinstance(obj, str):
        return IPV4_RE.sub('<IP_REDACTED>', JWT_RE.sub('<JWT_REDACTED>', obj))
    return obj


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 1
    har_dir, out_path = sys.argv[1], sys.argv[2]
    pseudo = Pseudonymiser()

    entries = []
    for f in sorted(glob.glob(os.path.join(har_dir, '*.har'))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        entries.extend(d['log']['entries'])
    entries.sort(key=lambda e: e.get('startedDateTime', ''))

    auction = None
    events = []
    seen_clock = set()

    for e in entries:
        url = e['request']['url']
        started = e.get('startedDateTime')
        body = e['response'].get('content', {}).get('text', '')

        # --- auction configuration (prefer the full variant) ---
        if '/auction/GetAuction/' in url and body:
            try:
                auction = json.loads(body)
            except Exception:
                pass
        elif '/auction/GetAuctionSimple/' in url and body and auction is None:
            try:
                auction = json.loads(body)
            except Exception:
                pass

        # --- our own bid attempts ---
        if '/bid/ValidateBid' in url:
            try:
                req = json.loads(e['request']['postData']['text'])
                res = json.loads(body) if body else {}
            except Exception:
                continue
            events.append({
                'at': started,
                'type': 'self.validate_bid',
                'request': {
                    'totalValue': req.get('totalValue'),
                    'items': [{
                        'itemId': i.get('itemId'),
                        'lotInternalId': i.get('lotInternalId'),
                        'bidValue': i.get('bidValue'),
                        'totalValue': i.get('totalValue'),
                        'quantity': i.get('quantity'),
                    } for i in req.get('items', [])],
                },
                'response': {
                    'isValid': res.get('isValid'),
                    'errorType': res.get('errorType'),
                    'errorMessageLocale': res.get('errorMessageLocale'),
                    'minBid': res.get('minBid'),
                    'currentRankingPosition': res.get('currentRankingPosition'),
                },
            })

        # --- the observable bid book ---
        if '/bid/GetSupplierBids/' in url and body:
            try:
                bids = json.loads(body)
            except Exception:
                continue
            if not bids:
                continue
            events.append({
                'at': started,
                'type': 'bidbook.snapshot',
                'bids': scrub(bids, pseudo),
            })

        # --- realtime events ---
        if '/signalr/' in url and body:
            try:
                data = json.loads(body)
            except Exception:
                continue
            for m in (data.get('M') or []):
                if not isinstance(m, dict):
                    continue
                hub, meth, args = m.get('H'), m.get('M'), m.get('A')
                if meth == 'ReceiveClock':
                    clk = (args or [None])[0]
                    if clk in seen_clock:
                        continue
                    seen_clock.add(clk)
                    events.append({'at': started, 'type': 'clock.tick', 'serverTime': clk})
                elif meth == 'newData':
                    events.append({'at': started, 'type': 'auction.new_data', 'flags': args})
                elif meth in ('onConnected', 'onNewUserConnected', 'onUserDisconnected'):
                    events.append({
                        'at': started,
                        'type': f'participants.{meth}',
                        'participants': scrub(args, pseudo),
                    })

        # --- audit trail ---
        if 'GetEventLogs' in url and body:
            try:
                rows = json.loads(body).get('data') or []
            except Exception:
                continue
            for r in rows:
                events.append({
                    'at': started,
                    'type': 'audit.entry',
                    'registerDate': r.get('registerDate'),
                    'eventType': r.get('eventHistoryTypeCode'),
                    'eventSubType': r.get('eventHistorySubTypeCode'),
                    'description': IPV4_RE.sub('<IP_REDACTED>', r.get('description') or ''),
                    'userCode': pseudo(r.get('userCode')),
                })

    events.sort(key=lambda e: e['at'])

    # De-duplicate audit rows, which are re-fetched on every poll.
    deduped, seen = [], set()
    for ev in events:
        if ev['type'] == 'audit.entry':
            k = (ev['registerDate'], ev['eventSubType'], ev['description'])
            if k in seen:
                continue
            seen.add(k)
        deduped.append(ev)
    events = deduped

    if not events:
        print('No replayable events found', file=sys.stderr)
        return 1

    t0 = ts(events[0]['at'])
    for ev in events:
        ev['offsetMs'] = int((ts(ev['at']) - t0).total_seconds() * 1000)

    cfg = {}
    if auction:
        keep = ('id', 'auctionVersionCode', 'auctionEvaluationTypeCode', 'auctionTypeCode',
                'minimumBidType', 'auctionStateCode', 'auctionExecutionStateCode',
                'currencyCode', 'roundTypeCode', 'totalNum', 'decimalNum', 'startPrice',
                'startDate', 'endDate', 'originalStartDate', 'originalEndDate',
                'extraTimeAutomaticAllow', 'extraTimePeriod',
                'extraTimeLastMinutesStartPeriod', 'activateExtraTimeWinner',
                'extraTimeManualAllow', 'extraTimeMaximumBids', 'pauseAllow',
                'allowBidsBellowBestBid', 'showRanking', 'showBestBidsForAllBids',
                'showBestBidsForSelfBids', 'hideBidderCurrentBid', 'noValueRank',
                'canSupplierSeeTotalParticipants', 'initialValueDifferentPerBidder',
                'testAuction')
        cfg = {k: auction.get(k) for k in keep if k in auction}
        cfg['registeredBidderCount'] = len(auction.get('bidders') or [])
        lots = []
        for lot in (auction.get('biddingList') or []):
            lots.append({
                'lotInternalId': lot.get('internalId'),
                'lotName': lot.get('lotName'),
                'items': [{
                    'itemId': it.get('id'),
                    'itemInternalReference': it.get('itemInternalReference'),
                    'itemDescription': it.get('itemDescription'),
                    'itemUnit': it.get('itemUnit'),
                    'itemQuantity': it.get('itemQuantity'),
                    'itemInitialPrice': it.get('itemInitialPrice'),
                    'itemInitialUnitPrice': it.get('itemInitialUnitPrice'),
                    'minBidDifference': it.get('minBidDifference'),
                } for it in (lot.get('biddingPriceListItems') or [])],
            })
        cfg['lots'] = lots

    counts = {}
    for ev in events:
        counts[ev['type']] = counts.get(ev['type'], 0) + 1

    fixture = {
        '$schema': 'starcoop/auction-replay/v1',
        'description': (
            'Replayable event stream from a real SECOP II subasta inversa, captured '
            '2026-03-02 (tender SASI-001-2026). Competitor company codes are replaced '
            'with stable pseudonyms; credentials and IP addresses are redacted. '
            'Regenerate with tools/build_replay_fixture.py.'
        ),
        'auction': cfg,
        'stats': {
            'eventCount': len(events),
            'eventTypes': counts,
            'distinctCompetitors': pseudo.count,
            'spanMs': events[-1]['offsetMs'],
        },
        'events': events,
    }

    blob = json.dumps(fixture, indent=2, ensure_ascii=False)
    if JWT_RE.search(blob):
        print('ABORT: fixture contains a JWT', file=sys.stderr)
        return 1
    open(out_path, 'w', encoding='utf-8').write(blob)

    print(f'wrote {out_path}')
    print(f'  events: {len(events)} over {events[-1]["offsetMs"] / 1000:.0f}s')
    print(f'  competitors pseudonymised: {pseudo.count}')
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f'  {v:5d}  {k}')
    return 0


sys.exit(main())
