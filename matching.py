"""Resolve one exact sports event and contract with structured diagnostics."""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import logging
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from names import name_keys, normalize, participant_name, same_name, variants
from series_map import (candidate_series, guessed_sports, series_sport,
                        tennis_series)


log = logging.getLogger(__name__)


MATCHED = "MATCHED"
SPORT_NOT_DETECTED = "SPORT_NOT_DETECTED"
LEAGUE_NOT_DETECTED = "LEAGUE_NOT_DETECTED"
UNSUPPORTED_MARKET_SCOPE = "UNSUPPORTED_MARKET_SCOPE"
SERIES_NOT_CONFIGURED = "SERIES_NOT_CONFIGURED"
NO_EVENTS_RETURNED = "NO_EVENTS_RETURNED"
EVENT_PARTICIPANTS_MISMATCH = "EVENT_PARTICIPANTS_MISMATCH"
EVENT_DATE_MISMATCH = "EVENT_DATE_MISMATCH"
CONTRACT_NOT_FOUND = "CONTRACT_NOT_FOUND"
LINE_NOT_FOUND = "LINE_NOT_FOUND"
AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"

FAILURE_CODES = frozenset({
    SPORT_NOT_DETECTED, LEAGUE_NOT_DETECTED, UNSUPPORTED_MARKET_SCOPE,
    SERIES_NOT_CONFIGURED, NO_EVENTS_RETURNED, EVENT_PARTICIPANTS_MISMATCH,
    EVENT_DATE_MISMATCH, CONTRACT_NOT_FOUND, LINE_NOT_FOUND, AMBIGUOUS_MATCH,
})

SPORT_GROUPS = {
    "nfl": "football", "ncaaf": "football",
    "nba": "basketball", "wnba": "basketball",
    "nhl": "hockey", "mlb": "baseball",
    "atp": "tennis", "wta": "tennis",
    "cs2": "esports", "dota2": "esports", "lol": "esports",
    "valorant": "esports", "epl": "soccer", "ucl": "soccer",
    "uefa_nations": "soccer", "uefa": "soccer", "world_cup": "soccer",
    "intl_friendly": "soccer", "mls": "soccer", "soccer": "soccer",
}


@dataclass
class MatchResult:
    status: str
    reason: str
    event: dict | None = None
    market: dict | None = None
    side: str | None = None
    candidates: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def number(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def unsupported_scope(text, kind=None, scope="full_game"):
    """Reject scopes that lack an explicit series route.

    BO1/BO3/BO5 describes an esports match length, not a map-level market.
    Tournament names following a match are likewise safe descriptors.
    """
    lower = normalize(text)
    if re.search(r"\b(?:map|set|game|quarter|half|inning)\s*\d+\b", lower):
        return True
    if re.search(r"\b(?:2h|second half|2nd half|1q|2q|3q|4q)\b", lower):
        return True
    if re.search(r"\b(?:map winner|set winner|player prop|kills|rounds|corners|"
                 r"handicap sets|to win tournament|tournament winner|total maps)\b", lower):
        return True
    if "team total" in lower and scope != "team_total":
        return True
    if re.search(r"\b(?:1h|first half|1st half)\b", lower) and scope != "first_half":
        return True
    return False


def event_participants(event):
    title = event.get("title", "").split(":", 1)[0]
    parts = re.split(r"\s+(?:vs\.?|at|@)\s+", title, flags=re.I)
    return [part.strip() for part in parts] if len(parts) == 2 else []


MONTHS = {name: number for number, name in enumerate(
          ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
           "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def event_dates(event):
    """Return all defensible event dates across ticker, UTC, and US/Eastern.

    Sports alerts commonly arrive near midnight UTC. The ticker date is the
    exchange's scheduled date, while occurrence timestamps let us accept the
    equivalent Eastern date without guessing from Telegram text.
    """
    dates = set()
    ticker = event.get("event_ticker", "")
    match = re.search(r"-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})", ticker, re.I)
    if match:
        try:
            dates.add(date(2000 + int(match.group(1)), MONTHS[match.group(2).upper()],
                           int(match.group(3))))
        except ValueError:
            pass
    for key in ("occurrence_datetime", "strike_date"):
        value = event.get(key)
        if not value:
            continue
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            dates.add(parsed.date())
            if parsed.tzinfo is not None:
                dates.add(parsed.astimezone(ZoneInfo("America/New_York")).date())
        except (ValueError, ZoneInfoNotFoundError):
            continue
    return dates


def event_date(event):
    """Compatibility helper returning the exchange ticker date when present."""
    dates = event_dates(event)
    return min(dates) if dates else None


def total_contract_side(alert, market):
    """Map one exact full-game total contract to YES/NO."""
    if market.get("market_type", "binary") != "binary" or market.get("mve_collection_ticker"):
        return None
    line = number(alert.get("line"))
    strike = number(market.get("floor_strike"))
    if line is None or abs(line) % 1 != Decimal("0.5"):
        return None
    if market.get("strike_type") != "greater" or strike != line or market.get("cap_strike") is not None:
        return None
    selected = re.fullmatch(r"(over|under)(?:\s+(\d+(?:\.\d+)?))?", alert["side"], re.I)
    if not selected or (selected[2] and number(selected[2]) != line):
        return None
    return "yes" if selected[1].lower() == "over" else "no"


def team_total_contract_side(alert, market, event=None):
    direction = total_contract_side(alert, market)
    if direction is None:
        return None
    label = market.get("yes_sub_title", "")
    proposition = re.fullmatch(
        r"(.+?)\s+(?:over|more than)\s+([0-9.]+)\s+(?:points?|goals?|runs?)\s*(?:scored)?\??",
        label, re.I)
    if not proposition or number(proposition[2]) != number(alert.get("line")):
        return None
    tennis = bool(event and tennis_series(event.get("series_ticker")))
    return direction if participant_name(proposition[1], alert.get("team1"), tennis) else None


def contract_side(alert, market, event=None, require_open=True):
    """Use YES semantics. NO subtitles can repeat YES in real Kalshi data."""
    if require_open and market.get("status") not in ("active", "open"):
        return None
    if market.get("market_type", "binary") != "binary" or market.get("mve_collection_ticker"):
        return None
    kind, side = alert["market_type"], alert["side"]
    label = market.get("yes_sub_title", "")
    if kind == "game_winner":
        tennis = bool(event and tennis_series(event.get("series_ticker")))
        if alert.get("yes_no_proposition"):
            proposition = re.fullmatch(r"Will\s+(.+?)\s+win\??", label, re.I)
            label_subject = proposition.group(1) if proposition else label
            if not participant_name(label_subject, alert["team1"], tennis):
                return None
            return side.lower() if side.lower() in ("yes", "no") else None
        if not any(participant_name(side, p, tennis)
                   for p in (alert.get("team1"), alert.get("team2")) if p):
            return None
        return "yes" if participant_name(label, side, tennis) else None

    if kind == "total":
        if alert.get("market_scope") == "team_total":
            return team_total_contract_side(alert, market, event)
        return total_contract_side(alert, market)

    line = number(alert.get("line"))
    strike = number(market.get("floor_strike"))
    if line is None or abs(line) % 1 != Decimal("0.5"):
        return None
    if market.get("strike_type") != "greater" or strike != abs(line) or market.get("cap_strike") is not None:
        return None
    if kind == "spread":
        opponent = alert.get("team2")
        if not opponent and event:
            opponent = next((p for p in event_participants(event)
                             if not same_name(p, alert.get("team1"))), None)
        favorite = alert.get("team1") if line < 0 else opponent
        proposition = re.fullmatch(
            r"(.+?) wins by (?:over|more than) ([0-9.]+) (?:points?|maps?|games?|goals?|runs?)\??",
            label, re.I)
        tennis = bool(event and tennis_series(event.get("series_ticker")))
        if not proposition or not participant_name(proposition[1], favorite, tennis):
            return None
        if number(proposition[2]) != strike:
            return None
        if participant_name(side, alert.get("team1"), tennis):
            return "yes" if line < 0 else "no"
        if alert.get("team2") and participant_name(side, alert["team2"], tennis):
            return "no" if line < 0 else "yes"
    return None


class Matcher:
    def __init__(self, client):
        self.client = client
        # Exact participant identities retain family and event suffix across
        # winner/spread/total series. The suffix is only a narrowing signal;
        # participant and contract checks still run in full.
        self.event_context = {}
        # Single-team scopes ("Spread: Lions", team totals) can reuse context
        # only while that participant maps to one remembered event. Multiple
        # remembered games deliberately disable this shortcut.
        self.participant_context = {}

    @staticmethod
    def _participant_keys(left, right):
        if not left or not right:
            return set()
        return {tuple(sorted((a, b))) for a in name_keys(left) for b in name_keys(right)}

    @staticmethod
    def _event_suffix(event):
        ticker = str(event.get("event_ticker") or "")
        series = str(event.get("series_ticker") or "")
        return ticker[len(series):] if series and ticker.startswith(series) else ""

    def _remember_event(self, event):
        participants = event_participants(event)
        sport = series_sport(event.get("series_ticker"))
        suffix = self._event_suffix(event)
        if len(participants) != 2 or not sport:
            return
        for key in self._participant_keys(*participants):
            self.event_context.setdefault(key, set()).add((sport, suffix))
        for participant in participants:
            for key in name_keys(participant):
                self.participant_context.setdefault(key, set()).add((sport, suffix))

    def _context(self, alert):
        entries = set()
        for key in self._participant_keys(alert.get("team1"), alert.get("team2")):
            entries.update(self.event_context.get(key, set()))
        if not entries and alert.get("team1") and not alert.get("team2"):
            single_entries = set()
            for key in name_keys(alert["team1"]):
                single_entries.update(self.participant_context.get(key, set()))
            if len(single_entries) == 1:
                entries = single_entries
        sports = {sport for sport, _ in entries}
        suffixes = {suffix for _, suffix in entries if suffix}
        return (tuple(sports) if len(sports) == 1 else (), suffixes)

    @staticmethod
    def _candidate_score(alert, event):
        wanted = set(normalize(f"{alert.get('team1') or ''} {alert.get('team2') or ''}").split())
        found = set(normalize(" ".join(event_participants(event))).split())
        return len(wanted & found) / len(wanted | found) if wanted and found else 0.0

    def _diagnostics(self, alert, series, preferred_sports):
        sports, source = guessed_sports(alert, preferred_sports)
        groups = sorted({SPORT_GROUPS.get(sport) for sport in sports
                         if SPORT_GROUPS.get(sport)})
        return {
            "sport_league_guess": list(sports),
            "detected_sport": groups[0] if len(groups) == 1 else None,
            "detected_league": sports[0] if len(sports) == 1 else None,
            "sport_guess_source": source,
            "market_type": alert.get("market_type"),
            "market_scope": alert.get("market_scope", "full_game"),
            "candidate_series_checked": list(series),
            "normalized_participants": {
                "team1": list(variants(alert.get("team1"))),
                "team2": list(variants(alert.get("team2"))),
            },
            "closest_candidate_events": [],
            "series_results": [],
            "event_rejections": [],
            "event_matched_contract_failed": False,
            "contracts_checked": 0,
        }

    @staticmethod
    def _failure(status, reason, diagnostics, **values):
        if status not in FAILURE_CODES:
            raise ValueError(f"Unknown matcher failure code: {status}")
        result = MatchResult(status, reason, diagnostics=diagnostics, **values)
        log.info("Sports match failed: status=%s reason=%s diagnostics=%s",
                 status.upper(), reason, diagnostics)
        return result

    def match(self, alert):
        preferred_sports, context_suffixes = self._context(alert)
        series = candidate_series(alert, preferred_sports)
        diagnostics = self._diagnostics(alert, series, preferred_sports)
        scope = alert.get("market_scope", "full_game")
        if unsupported_scope(alert.get("market", ""), alert.get("market_type"), scope):
            return self._failure(UNSUPPORTED_MARKET_SCOPE,
                                 "Unsupported partial-game or proposition scope",
                                 diagnostics)
        if alert.get("market_type") == "unknown":
            return self._failure(SPORT_NOT_DETECTED, "Market type/sport was not detected",
                                 diagnostics)
        if not series:
            status = (LEAGUE_NOT_DETECTED if not diagnostics["sport_league_guess"]
                      else SERIES_NOT_CONFIGURED)
            return self._failure(status, "No configured series for market league/type/scope",
                                 diagnostics)
        proposition = bool(alert.get("yes_no_proposition"))
        team_total = alert.get("market_type") == "total" and scope == "team_total"
        if not alert.get("team1") or (alert.get("market_type") != "spread" and
                                      not alert.get("team2") and not proposition and not team_total):
            return self._failure(LEAGUE_NOT_DETECTED,
                                 "Alert is missing required participant context",
                                 diagnostics)
        if alert.get("team2") and same_name(alert["team1"], alert["team2"]):
            return self._failure(AMBIGUOUS_MATCH, "Participants are indistinguishable", diagnostics)
        if alert.get("market_type") != "game_winner":
            line = number(alert.get("line"))
            if line is None or abs(line) % 1 != Decimal("0.5"):
                return self._failure(UNSUPPORTED_MARKET_SCOPE,
                                     "An explicit half-point/map line is required",
                                     diagnostics)
        if alert.get("side", "").lower() in ("yes", "no") and not proposition:
            return self._failure(EVENT_PARTICIPANTS_MISMATCH,
                                 "Alert must name the selected participant",
                                 diagnostics)

        events, near = {}, []
        total_events = 0
        date_mismatches = 0
        for ticker in series:
            tennis = tennis_series(ticker)
            returned = self.client.recent_series_events(ticker)
            series_diag = {
                "series_ticker": ticker, "events_returned": len(returned),
                "participant_matches": 0, "date_mismatches": 0,
            }
            diagnostics["series_results"].append(series_diag)
            total_events += len(returned)
            for event in returned:
                if event.get("series_ticker") != ticker:
                    continue
                score = self._candidate_score(alert, event)
                rejection = None
                participants = event_participants(event)
                if len(participants) != 2:
                    rejection = "event title does not contain exactly two participants"
                elif alert.get("team2"):
                    orientations = (participants, participants[::-1])
                    valid = any(participant_name(a, alert["team1"], tennis)
                                and participant_name(b, alert["team2"], tennis)
                                for a, b in orientations)
                    if not valid:
                        rejection = "participant identities do not match exactly"
                elif not any(participant_name(p, alert["team1"], tennis)
                             for p in participants):
                    rejection = "named participant does not match event"
                if rejection is None and proposition:
                    try:
                        wanted_date = date.fromisoformat(alert["market_date"])
                    except (TypeError, ValueError):
                        return self._failure(UNSUPPORTED_MARKET_SCOPE,
                                             "Yes/no winner proposition requires a valid ISO date",
                                             diagnostics)
                    if wanted_date not in event_dates(event):
                        rejection = "event date mismatch"
                        date_mismatches += 1
                        series_diag["date_mismatches"] += 1
                near.append((score, event, rejection))
                if rejection:
                    continue
                series_diag["participant_matches"] += 1
                event_ticker = event.get("event_ticker")
                if event_ticker:
                    events[event_ticker] = event

        closest = sorted(near, key=lambda item: (-item[0], item[1].get("event_ticker", "")))[:5]
        diagnostics["closest_candidate_events"] = [
            {"event_ticker": event.get("event_ticker"), "series_ticker": event.get("series_ticker"),
             "title": event.get("title"), "token_score": round(score, 3),
             "rejection_reason": rejection}
            for score, event, rejection in closest
        ]
        diagnostics["event_rejections"] = [
            {"event_ticker": event.get("event_ticker"), "title": event.get("title"),
             "reason": rejection}
            for _, event, rejection in closest if rejection
        ]
        if total_events == 0:
            return self._failure(NO_EVENTS_RETURNED,
                                 "Configured series returned no recent events", diagnostics)
        if context_suffixes:
            narrowed = {ticker: event for ticker, event in events.items()
                        if self._event_suffix(event) in context_suffixes}
            if narrowed:
                events = narrowed
                diagnostics["event_context_suffixes"] = sorted(context_suffixes)

        ids = sorted(events)
        if not ids:
            status = EVENT_DATE_MISMATCH if proposition and date_mismatches else EVENT_PARTICIPANTS_MISMATCH
            reason = ("Participant event found, but its exchange date did not match"
                      if status == EVENT_DATE_MISMATCH else
                      "Events returned, but participant identities did not match exactly")
            return self._failure(status, reason, diagnostics)
        if len(ids) != 1:
            return self._failure(AMBIGUOUS_MATCH,
                                 "Multiple events match; alert needs date/opponent context",
                                 diagnostics, candidates=ids)
        event = events[ids[0]]
        self._remember_event(event)
        resolved_league = series_sport(event.get("series_ticker"))
        if resolved_league:
            diagnostics["detected_league"] = resolved_league
            diagnostics["detected_sport"] = SPORT_GROUPS.get(resolved_league)
        diagnostics["matched_event_ticker"] = ids[0]
        diagnostics["matched_event_title"] = event.get("title")

        matches = {}
        markets = self.client.event_markets(ids[0], status=None)
        diagnostics["contracts_checked"] = len(markets)
        for market in markets:
            if market.get("event_ticker") != ids[0] or not market.get("ticker"):
                continue
            side = contract_side(alert, market, event, require_open=False)
            if side:
                matches[(market["ticker"], side)] = market
        if not matches:
            line = number(alert.get("line"))
            diagnostics["event_matched_contract_failed"] = True
            diagnostics["requested_line"] = float(line) if line is not None else None
            reason = (f"Event found but exact {alert['market_type']} line {line} is missing"
                      if alert.get("market_type") in ("spread", "total") else
                      "Event found but winner contract is missing")
            status = LINE_NOT_FOUND if alert.get("market_type") in ("spread", "total") else CONTRACT_NOT_FOUND
            return self._failure(status, reason, diagnostics, event=event)
        if len(matches) != 1:
            return self._failure(AMBIGUOUS_MATCH, "Multiple exact contracts match the event",
                                 diagnostics, event=event,
                                 candidates=[f"{ticker}:{side}" for ticker, side in matches])
        (_, side), market = next(iter(matches.items()))
        diagnostics["matched_contract_ticker"] = market.get("ticker")
        diagnostics["matched_contract_status"] = market.get("status")
        return MatchResult(MATCHED, "Unique series, event, contract and side",
                           event, market, side, diagnostics=diagnostics)

    def match_total(self, alert, series):
        """Compatibility entry point; all matching now shares one exact pipeline."""
        return self.match(alert)
