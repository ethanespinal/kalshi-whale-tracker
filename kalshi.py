"""Read-only Kalshi client with bounded, cached series lookup."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
from urllib.parse import quote

import requests

from config import Settings

BASE_URL = "https://external-api.kalshi.com/trade-api/v2"


class LookupUnavailable(RuntimeError):
    """Incomplete data must never be treated as a unique market match."""


class KalshiClient:
    def __init__(self, settings=None, session=None, clock=time.monotonic,
                 sleep=time.sleep, wall_clock=time.time):
        self.settings = settings or Settings()
        self.session = session or requests.Session()
        self.clock, self.sleep = clock, sleep
        self.wall_clock = wall_clock
        self.next_request = 0
        self.cooldown = 0
        self.cache = {}

    def close(self):
        self.session.close()

    def get(self, path, params=None):
        # This client has no authenticated endpoints and no write methods.
        if self.clock() < self.cooldown:
            raise LookupUnavailable("Kalshi cooldown active; retry later")
        for attempt in range(3):
            self.sleep(max(0, self.next_request - self.clock()))
            self.next_request = self.clock() + self.settings.request_interval
            try:
                response = self.session.get(BASE_URL + path, params=params, timeout=(5, 15))
            except requests.RequestException as exc:
                self.cooldown = self.clock() + 30
                raise LookupUnavailable("Kalshi network error; 30 second cooldown") from exc
            if response.status_code == 429:
                value = response.headers.get("Retry-After", "30")
                try:
                    delay = float(value)
                except ValueError:
                    try:
                        delay = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
                    except (ValueError, TypeError):
                        delay = 30
                # Do not retry a 429 in this alert or shorten Retry-After.
                self.cooldown = self.clock() + max(30, delay)
                raise LookupUnavailable("Kalshi rate limited; cooldown set")
            if response.status_code >= 500:
                if attempt < 2:
                    self.sleep(2 ** attempt)
                    continue
                self.cooldown = self.clock() + 30
                raise LookupUnavailable("Kalshi server unavailable")
            try:
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise LookupUnavailable("Invalid Kalshi response") from exc
            if not isinstance(data, dict):
                raise LookupUnavailable("Expected a Kalshi JSON object")
            return data
        raise LookupUnavailable("Kalshi request exhausted")

    def collection(self, path, key, params):
        cache_key = (path, tuple(sorted(params.items())))
        cached = self.cache.get(cache_key)
        if cached and self.clock() < cached[0]:
            return cached[1]
        rows, cursors = [], set()
        params = dict(params)
        for _ in range(self.settings.max_pages):
            data = self.get(path, params)
            page = data.get(key)
            if not isinstance(page, list) or not all(isinstance(row, dict) for row in page):
                raise LookupUnavailable(f"Missing or invalid {key}")
            rows.extend(page)
            cursor = data.get("cursor")
            if not cursor:
                # Cache empty results too, but never cache partial pagination.
                now = self.clock()
                self.cache = {k: v for k, v in self.cache.items() if v[0] > now}
                self.cache[cache_key] = (now + self.settings.cache_ttl, rows)
                return rows
            if cursor in cursors:
                raise LookupUnavailable("Repeated pagination cursor")
            cursors.add(cursor)
            params["cursor"] = cursor
        raise LookupUnavailable("Series/event page limit reached; lookup incomplete")

    def series_events(self, series):
        if not series:
            raise ValueError("A series ticker is required")
        return self.collection("/events", "events", {
            "series_ticker": series, "status": "open", "limit": 200,
        })

    def recent_series_events(self, series, lookback_seconds=86400):
        """Open and recently closed events in one bounded, cacheable query."""
        if not series:
            raise ValueError("A series ticker is required")
        # Bucket the timestamp so all alerts in one cache window share a key.
        bucket = int(self.wall_clock() // self.settings.cache_ttl * self.settings.cache_ttl)
        return self.collection("/events", "events", {
            "series_ticker": series,
            "min_close_ts": bucket - lookback_seconds,
            "limit": 200,
        })

    def event_markets(self, ticker, status="open"):
        if not ticker:
            raise ValueError("An event ticker is required")
        params = {"event_ticker": ticker, "limit": 1000}
        if status:
            params["status"] = status
        return self.collection("/markets", "markets", params)

    def market(self, ticker):
        # Intentionally uncached: quotes and settlement are always refreshed.
        data = self.get("/markets/" + quote(ticker, safe=""))
        market = data.get("market")
        if not isinstance(market, dict) or market.get("ticker") != ticker:
            raise LookupUnavailable("Missing or mismatched market response")
        return market
