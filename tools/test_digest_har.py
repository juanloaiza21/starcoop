#!/usr/bin/env python3
"""Precision tests for the digest_har.py redactor.

Motivated by a real defect: the original IPv4 pattern matched any four dotted
numerals and rewrote the preserved User-Agent header's Chrome version
("Chrome/143.0.0.0") to "<IP_REDACTED>". These tests pin both directions:

- no FALSE POSITIVES on the real preserved-header corpus (browser version
  tokens, quality factors, decree article numbers);
- no FALSE NEGATIVES on the things the redactor exists to catch (IPv4
  addresses in audit `ipAddress` fields and URL hosts, JWTs, rival NITs).

All IPv4 literals below are RFC 5737 documentation addresses (203.0.113.0/24,
198.51.100.0/24) — no real capture IP appears in this file.
IPv6 is NOT covered by the redactor: no IPv6 literal appears anywhere in the
captured corpus (the audit `ipAddress` field is IPv4). If a future capture
contains IPv6, add a pattern and tests before regenerating the contract.
"""
import unittest

from digest_har import IPV4_RE, redact

# The exact preserved-header block sent to the auction API, as regenerated for
# docs/01-api-contract.md §3 (values already credential-free at capture time).
REAL_HEADER_CORPUS = """\
Accept: application/json, text/plain, */*
Accept-Encoding: gzip, deflate, br, zstd
Accept-Language: es-ES,es;q=0.9,en;q=0.8
Cache-Control: max-age=0
Connection: keep-alive
Host: subastas.secop.gov.co
If-Modified-Since: Mon, 26 Jul 1997 05:00:00 GMT
Referer: https://subastas.secop.gov.co/eauctions.site/?auctionId=e66a68a3-a14b-4d8a-8f8e-fd6333ade7ca&languageCode=es-CO
Sec-Fetch-Dest: empty
Sec-Fetch-Mode: cors
Sec-Fetch-Site: same-origin
User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36
sec-ch-ua: "Google Chrome";v="143", "Chromium";v="143", "Not A(Brand";v="24"
sec-ch-ua-mobile: ?0
sec-ch-ua-platform: "Windows"
"""


class NoFalsePositives(unittest.TestCase):
    def test_full_header_corpus_untouched(self):
        # The regression that motivated this file: nothing in the real header
        # block is an IP address, so redact() must be the identity on it
        # (modulo JWT/NIT patterns, which also do not occur here).
        self.assertEqual(redact(REAL_HEADER_CORPUS), REAL_HEADER_CORPUS)

    def test_chrome_version_token(self):
        s = 'Chrome/143.0.0.0 Safari/537.36'
        self.assertEqual(redact(s), s)

    def test_applewebkit_version_token(self):
        s = 'AppleWebKit/537.36 (KHTML, like Gecko)'
        self.assertEqual(redact(s), s)

    def test_decree_article_numbers(self):
        s = 'Decreto 1082 de 2015, articulo 2.2.1.2.1.2.6'
        self.assertEqual(redact(s), s)

    def test_quality_factors(self):
        s = 'es-ES,es;q=0.9,en;q=0.8'
        self.assertEqual(redact(s), s)

    def test_dotted_run_longer_than_four(self):
        s = 'assembly 4.0.30319.42000.1.2'  # >4 groups: no 4-octet window is free-standing
        self.assertNotIn('<IP_REDACTED>', redact(s))

    def test_octets_over_255_not_matched(self):
        s = 'build 10.0.19045.1234'
        self.assertEqual(redact(s), s)

    def test_digits_adjoining(self):
        s = 'x1203.0.113.451y'
        self.assertEqual(redact(s), s)


class NoFalseNegatives(unittest.TestCase):
    def test_audit_ip_address_field(self):
        self.assertEqual(
            redact('"ipAddress": "203.0.113.45"'),
            '"ipAddress": "<IP_REDACTED>"',
        )

    def test_bare_ip(self):
        self.assertEqual(redact('from 198.51.100.7 today'),
                         'from <IP_REDACTED> today')

    def test_ip_as_url_host(self):
        # "//" of the scheme must not be mistaken for a version token's "/".
        self.assertEqual(redact('https://203.0.113.45/path'),
                         'https://<IP_REDACTED>/path')

    def test_ip_edge_octets(self):
        self.assertEqual(redact('255.255.255.255 and 0.0.0.0'),
                         '<IP_REDACTED> and <IP_REDACTED>')

    def test_jwt_redacted(self):
        fake_jwt = 'eyJ' + 'A' * 20 + '.' + 'B' * 30 + '.' + 'C' * 40
        self.assertEqual(redact(f'token={fake_jwt}'), 'token=<JWT_REDACTED>')

    def test_rival_company_code_pseudonymised(self):
        out = redact('"companyCode": "712345678"')
        self.assertNotIn('712345678', out)
        self.assertIn('<COMPETITOR_', out)

    def test_own_company_code_kept(self):
        s = '"userCode": "700961121"'
        self.assertEqual(redact(s), s)


class PatternShape(unittest.TestCase):
    def test_exactly_four_octets_required(self):
        self.assertIsNone(IPV4_RE.search('537.36'))
        self.assertIsNone(IPV4_RE.search('1.2.3'))
        self.assertIsNotNone(IPV4_RE.search('203.0.113.45'))


if __name__ == '__main__':
    unittest.main()
