from dataclasses import replace
import json
import unittest

import requests

from config import Settings
from kalshi import KalshiClient, LookupUnavailable


class Clock:
    def __init__(self):
        self.now = 0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def response(data=None, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(data if data is not None else {}).encode()
    result.headers.update(headers or {})
    return result


class Session:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {}), timeout))
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class ClientTests(unittest.TestCase):
    def client(self, replies, **settings):
        self.clock = Clock()
        self.session = Session(replies)
        return KalshiClient(replace(Settings(), **settings), self.session, self.clock, self.clock.sleep)

    def test_series_filter_pagination_cache_and_expiry(self):
        client = self.client([response({'events': [{'id': 1}], 'cursor': 'next'}),
                              response({'events': [{'id': 2}], 'cursor': ''}),
                              response({'events': []})])
        self.assertEqual(len(client.series_events('KXCS2GAME')), 2)
        self.assertEqual(len(client.series_events('KXCS2GAME')), 2)
        self.assertEqual(len(self.session.calls), 2)
        for _, params, _ in self.session.calls:
            self.assertEqual(params['series_ticker'], 'KXCS2GAME')
            self.assertEqual(params['status'], 'open')
        self.assertEqual(self.session.calls[1][1]['cursor'], 'next')
        self.assertGreaterEqual(self.clock.now, .6)
        self.clock.now += 121
        self.assertEqual(client.series_events('KXCS2GAME'), [])

    def test_empty_cache_and_event_market_filter(self):
        client = self.client([response({'markets': []})])
        self.assertEqual(client.event_markets('EVENT'), [])
        self.assertEqual(client.event_markets('EVENT'), [])
        self.assertEqual(len(self.session.calls), 1)
        self.assertEqual(self.session.calls[0][1]['event_ticker'], 'EVENT')

    def test_recent_series_events_are_close_bounded_and_cacheable(self):
        client = self.client([response({'events': [{'event_ticker': 'RECENT'}]})])
        client.wall_clock = lambda: 1_000_099
        self.assertEqual(client.recent_series_events('KXUEFANLTOTAL')[0]['event_ticker'], 'RECENT')
        self.assertEqual(client.recent_series_events('KXUEFANLTOTAL')[0]['event_ticker'], 'RECENT')
        self.assertEqual(len(self.session.calls), 1)
        params = self.session.calls[0][1]
        self.assertEqual(params['series_ticker'], 'KXUEFANLTOTAL')
        self.assertEqual(params['min_close_ts'], 913680)
        self.assertNotIn('status', params)

    def test_all_status_event_markets_omit_status_filter(self):
        client = self.client([response({'markets': []})])
        self.assertEqual(client.event_markets('EVENT', status=None), [])
        self.assertNotIn('status', self.session.calls[0][1])

    def test_partial_page_limit_is_not_success_or_cached(self):
        client = self.client([response({'events': [{'id': 1}], 'cursor': 'more'})], max_pages=1)
        with self.assertRaises(LookupUnavailable):
            client.series_events('SERIES')
        self.assertEqual(client.cache, {})

    def test_repeated_cursor(self):
        client = self.client([response({'events': [], 'cursor': 'same'}), response({'events': [], 'cursor': 'same'})])
        with self.assertRaises(LookupUnavailable):
            client.series_events('SERIES')
        self.assertEqual(len(self.session.calls), 2)

    def test_429_honors_cooldown_across_series_without_retry_storm(self):
        client = self.client([response(status=429, headers={'Retry-After': '120'}), response({'events': []})])
        with self.assertRaises(LookupUnavailable):
            client.series_events('A')
        self.clock.now += 60
        with self.assertRaises(LookupUnavailable):
            client.series_events('B')
        self.assertEqual(len(self.session.calls), 1)
        self.clock.now += 61
        self.assertEqual(client.series_events('B'), [])

    def test_transient_server_error_backoff(self):
        client = self.client([response(status=503), response(status=502), response({'events': []})])
        self.assertEqual(client.series_events('A'), [])
        self.assertEqual(len(self.session.calls), 3)
        self.assertGreaterEqual(self.clock.now, 3)

    def test_server_failure_bounded(self):
        client = self.client([response(status=503)] * 3)
        with self.assertRaises(LookupUnavailable):
            client.series_events('A')
        self.assertEqual(len(self.session.calls), 3)

    def test_network_failure_cooldown(self):
        client = self.client([requests.Timeout('timeout')])
        with self.assertRaises(LookupUnavailable):
            client.series_events('A')
        with self.assertRaises(LookupUnavailable):
            client.series_events('B')
        self.assertEqual(len(self.session.calls), 1)

    def test_malformed_response_not_cached(self):
        for data in ({}, {'events': 'bad'}, []):
            client = self.client([response(data)])
            with self.assertRaises(LookupUnavailable):
                client.series_events('A')
            self.assertFalse(client.cache)

    def test_no_unfiltered_events(self):
        client = self.client([])
        with self.assertRaises(ValueError):
            client.series_events('')
        with self.assertRaises(ValueError):
            client.event_markets('')
        self.assertEqual(self.session.calls, [])

    def test_quote_is_uncached_and_checks_identity(self):
        client = self.client([response({'market': {'ticker': 'T'}}), response({'market': {'ticker': 'WRONG'}})])
        self.assertEqual(client.market('T')['ticker'], 'T')
        with self.assertRaises(LookupUnavailable):
            client.market('T')
        self.assertEqual(len(self.session.calls), 2)
