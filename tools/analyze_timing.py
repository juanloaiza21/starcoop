#!/usr/bin/env python3
"""Measure SECOP II platform latency and connection churn from HAR captures.

Backs the timing claims in SOT.md §4.3, §4.4 and §4.5, and the decisions D-11 to D-13
that follow from them. Re-run this against a fresh capture before trusting the numbers:
platform performance is exactly the kind of thing that changes.

Usage:
    python3 tools/analyze_timing.py <har-directory>
"""
import json
import glob
import os
import re
import sys
import collections


def parse_ts(s):
    """HAR/.NET timestamps carry more precision than Python accepts."""
    s = s.replace('Z', '+00:00')
    s = re.sub(r'\.(\d{6})\d+', r'.\1', s)
    from datetime import datetime
    return datetime.fromisoformat(s)


def pct(values, p):
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(int(len(v) * p), len(v) - 1)]


def classify(url):
    if 'signalr/send' in url:
        return 'signalr/send (short POST — true RTT)'
    if 'signalr/poll' in url:
        return 'signalr/poll (LONG POLL — not an RTT)'
    if 'signalr/connect' in url:
        return 'signalr/connect'
    if 'signalr/negotiate' in url:
        return 'signalr/negotiate'
    if 'signalr/start' in url:
        return 'signalr/start'
    if 'eauctions.api/v1' in url:
        return 'REST /v1/* (incl. ValidateBid)'
    return None


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 1
    har_dir = sys.argv[1]

    entries = []
    for f in sorted(glob.glob(os.path.join(har_dir, '*.har'))):
        try:
            d = json.load(open(f, encoding='utf-8'))
        except Exception:
            continue
        entries.extend(d['log']['entries'])
    if not entries:
        print('no HAR entries found', file=sys.stderr)
        return 1
    entries.sort(key=lambda e: e.get('startedDateTime', ''))

    # ---- latency by request class -------------------------------------------------
    buckets = collections.defaultdict(list)
    for e in entries:
        k = classify(e['request']['url'])
        if k:
            buckets[k].append(e['time'])

    print('=' * 78)
    print('LATENCY BY REQUEST CLASS (ms)')
    print('=' * 78)
    print(f'{"class":<40} {"n":>4} {"min":>7} {"p50":>7} {"p90":>8} {"max":>8}')
    for k in sorted(buckets, key=lambda x: -len(buckets[x])):
        v = buckets[k]
        print(f'{k:<40} {len(v):>4} {min(v):>7.0f} {pct(v, .5):>7.0f} '
              f'{pct(v, .9):>8.0f} {max(v):>8.0f}')

    send = buckets.get('signalr/send (short POST — true RTT)', [])
    rest = buckets.get('REST /v1/* (incl. ValidateBid)', [])
    if rest:
        print()
        print('SUBMIT PATH BUDGET — ValidateBid then SubmitBid is TWO sequential round trips')
        print(f'  p50: {2 * pct(rest, .5) / 1000:.1f}s    '
              f'p90: {2 * pct(rest, .9) / 1000:.1f}s    '
              f'worst observed: {2 * max(rest) / 1000:.1f}s')
        print('  => Commit the final bid while at least the p90 remains on the clock (D-11).')
        print('  => A timeout does NOT mean the bid failed; reconcile before retry (D-12).')

    poll = buckets.get('signalr/poll (LONG POLL — not an RTT)', [])
    if poll and send:
        print()
        print('WHY THE NTP MIDPOINT ESTIMATE IS INVALID HERE (D-13)')
        print(f'  long-poll hold: p50 {pct(poll, .5) / 1000:.1f}s, '
              f'p90 {pct(poll, .9) / 1000:.1f}s, max {max(poll) / 1000:.1f}s')
        print(f'  send RTT:       p50 {pct(send, .5) / 1000:.1f}s, '
              f'p90 {pct(send, .9) / 1000:.1f}s')
        print('  The server emits ReceiveClock at an arbitrary moment inside the hold, so')
        print('  assuming it was stamped at the midpoint of the poll is meaningless.')
        print('  Derive the clock offset from the short send round trip instead.')

    # ---- connection churn ---------------------------------------------------------
    connects = collections.Counter()
    disconnects = collections.Counter()
    roster = set()
    for e in entries:
        if '/signalr/' not in e['request']['url']:
            continue
        body = e['response'].get('content', {}).get('text', '')
        if not body:
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        for m in (data.get('M') or []):
            if not isinstance(m, dict):
                continue
            meth, args = m.get('M'), m.get('A') or []
            if meth == 'onConnected':
                for c in (args[0] if args else []):
                    if isinstance(c, dict):
                        roster.add(c.get('companyCode'))
            elif meth == 'onNewUserConnected':
                for c in args:
                    if isinstance(c, dict):
                        code = c.get('companyCode')
                        roster.add(code)
                        connects[code] += 1
            elif meth == 'onUserDisconnected':
                for c in args:
                    if isinstance(c, dict):
                        disconnects[c.get('companyCode')] += 1

    if roster:
        print()
        print('=' * 78)
        print('CONNECTION CHURN')
        print('=' * 78)
        print(f'  companies seen in the room:        {len(roster)}')
        print(f'  companies that dropped at least once: {len(disconnects)} '
              f'({100 * len(disconnects) / len(roster):.0f}%)')
        print(f'  total disconnect events:           {sum(disconnects.values())}')
        print(f'  total reconnect events:            {sum(connects.values())}')
        if connects:
            vals = sorted(connects.values())
            print(f'  reconnects per company: median {vals[len(vals) // 2]}, '
                  f'worst {max(vals)}')
        print()
        print('  Most of the room is fighting the connection rather than the auction.')
        print('  Staying connected is the edge — see SOT.md §4.3.')

    # ---- clock ticks --------------------------------------------------------------
    ticks = []
    for e in entries:
        body = e['response'].get('content', {}).get('text', '')
        if '/signalr/' not in e['request']['url'] or not body:
            continue
        try:
            data = json.loads(body)
        except Exception:
            continue
        for m in (data.get('M') or []):
            if isinstance(m, dict) and m.get('M') == 'ReceiveClock':
                ticks.append(parse_ts((m.get('A') or [''])[0]))
    if len(ticks) > 2:
        ticks.sort()
        gaps = [(ticks[i + 1] - ticks[i]).total_seconds()
                for i in range(len(ticks) - 1)]
        gaps = [g for g in gaps if 0 < g < 300]
        if gaps:
            print()
            print(f'  server clock ticks observed: {len(ticks)}, '
                  f'interval p50 {pct(gaps, .5):.1f}s, max {max(gaps):.1f}s')
    return 0


sys.exit(main())
