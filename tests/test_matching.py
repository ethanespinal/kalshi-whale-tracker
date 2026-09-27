from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from alert_parser import parse_alert
from matching import Matcher, contract_side
from samples import message


def event(series='KXNFLGAME', title='ATL Falcons vs GB Packers', ticker='EVENT'):
    return {'series_ticker': series, 'title': title, 'event_ticker': ticker}


def market(ticker='CONTRACT', label='ATL Falcons', event_ticker='EVENT', **extra):
    close_time = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat().replace('+00:00', 'Z')
    return {'ticker': ticker, 'event_ticker': event_ticker, 'yes_sub_title': label,
            'status': 'active', 'market_type': 'binary', 'close_time': close_time,
            'notional_value_dollars': '1.0000', 'yes_ask_dollars': '0.5000',
            'yes_ask_size_fp': '100', 'no_ask_dollars': '0.5000',
            'no_ask_size_fp': '100', **extra}


class FakeClient:
    def __init__(self, events, markets):
        self.events, self.markets = events, markets
        self.calls = []

    def series_events(self, series):
        self.calls.append(('series', series))
        return [e for e in self.events if e['series_ticker'] == series]

    def recent_series_events(self, series, lookback_seconds=86400):
        self.calls.append(('recent_series', series))
        return [e for e in self.events if e['series_ticker'] == series]

    def event_markets(self, ticker, status='open'):
        self.calls.append(('event', ticker))
        rows = [m for m in self.markets if m['event_ticker'] == ticker]
        if status:
            rows = [m for m in rows if m.get('status') in ('active', 'open')]
        return rows

    def market(self, ticker):
        self.calls.append(('market', ticker))
        return deepcopy(next(m for m in self.markets if m['ticker'] == ticker))


class MatchingTests(unittest.TestCase):
    def match(self, title, side, events, markets):
        client = FakeClient(events, markets)
        return Matcher(client).match(parse_alert(message(title, side)))

    def test_winner_aliases_reversed_order(self):
        result = self.match('NFL: Packers vs Falcons', 'Falcons', [event()], [market()])
        self.assertEqual((result.status, result.side), ('MATCHED', 'yes'))

    def test_dated_winner_proposition_yes_and_no(self):
        events = [event('KXINTLFRIENDLYGAME', 'England vs Germany',
                        'KXINTLFRIENDLYGAME-26SEP26ENGGER')]
        markets = [market('ENGLAND-WIN', label='England',
                          event_ticker='KXINTLFRIENDLYGAME-26SEP26ENGGER')]
        for whale_side, expected in (('Yes', 'yes'), ('No', 'no')):
            with self.subTest(side=whale_side):
                result = self.match('Will England win on 2026-09-26?', whale_side,
                                    events, markets)
                self.assertEqual((result.status, result.side), ('MATCHED', expected))
                self.assertEqual(result.market['ticker'], 'ENGLAND-WIN')

    def test_dated_proposition_rejects_wrong_or_unknown_event_date(self):
        market_ticker = 'KXINTLFRIENDLYGAME-26SEP27ENGGER'
        for candidate in (
            event('KXINTLFRIENDLYGAME', 'England vs Germany', market_ticker),
            event('KXINTLFRIENDLYGAME', 'England vs Germany', 'UNDATED-EVENT'),
        ):
            with self.subTest(ticker=candidate['event_ticker']):
                result = self.match('Will England win on 2026-09-26?', 'Yes',
                                    [candidate],
                                    [market('ENGLAND-WIN', label='England',
                                            event_ticker=candidate['event_ticker'])])
                self.assertEqual(result.status, 'EVENT_DATE_MISMATCH')

    def test_dated_proposition_accepts_eastern_date_across_utc_midnight(self):
        event_row = event('KXINTLFRIENDLYGAME', 'England vs Germany', 'UNDATED-EVENT')
        event_row['occurrence_datetime'] = '2026-09-27T00:30:00Z'
        result = self.match(
            'Will England win on 2026-09-26?', 'Yes', [event_row],
            [market('ENGLAND-WIN', label='England', event_ticker='UNDATED-EVENT')])
        self.assertEqual(result.status, 'MATCHED')

    def test_dated_proposition_remains_ambiguous_for_two_same_date_games(self):
        events = [
            event('KXINTLFRIENDLYGAME', 'England vs Germany',
                  'KXINTLFRIENDLYGAME-26SEP26ENGGER'),
            event('KXUEFAGAME', 'England vs France',
                  'KXUEFAGAME-26SEP26ENGFRA'),
        ]
        result = self.match('Will England win on 2026-09-26?', 'No', events, [])
        self.assertEqual(result.status, 'AMBIGUOUS_MATCH')
        self.assertEqual(len(result.candidates), 2)

    def test_yes_no_proposition_requires_exact_subject_contract(self):
        event_row = event('KXINTLFRIENDLYGAME', 'England vs Germany',
                          'KXINTLFRIENDLYGAME-26SEP26ENGGER')
        for label in ('England U21', 'Germany', 'Will Germany win?'):
            with self.subTest(label=label):
                result = self.match('Will England win on 2026-09-26?', 'Yes', [event_row],
                    [market('OTHER', label=label, event_ticker=event_row['event_ticker'])])
                self.assertEqual(result.status, 'CONTRACT_NOT_FOUND')

    def test_regular_matchup_still_rejects_generic_yes_no(self):
        result = self.match('NFL: Falcons vs Packers', 'Yes', [event()], [market()])
        self.assertEqual(result.status, 'EVENT_PARTICIPANTS_MISMATCH')

    def test_substring_collision_rejected(self):
        result = self.match('NFL: Falcons vs Packers', 'Falcons',
                            [event(title='Falcons Academy vs Packers')], [market()])
        self.assertEqual(result.status, 'EVENT_PARTICIPANTS_MISMATCH')

    def test_duplicate_matchups_are_ambiguous(self):
        result = self.match('NFL: Falcons vs Packers', 'Falcons',
                            [event(), event(ticker='TOMORROW')], [market()])
        self.assertEqual(result.status, 'AMBIGUOUS_MATCH')

    def test_ohio_state_spread_both_sides_and_exact_line(self):
        events = [event('KXNCAAFSPREAD', 'Ohio State Buckeyes vs Marshall')]
        markets = [market(label='Ohio State wins by over 27.5 points', floor_strike=27.5, strike_type='greater'),
                   market('WRONG', label='Ohio State wins by over 24.5 points', floor_strike=24.5, strike_type='greater')]
        for side, expected in [('Ohio State', 'yes'), ('Marshall', 'no')]:
            result = self.match('Spread: Ohio State (-27.5)', side, events, markets)
            self.assertEqual((result.status, result.side, result.market['ticker']), ('MATCHED', expected, 'CONTRACT'))

    def test_positive_spread_infers_opponent_only_in_unique_event(self):
        result = self.match('Spread: Ohio State (+27.5)', 'Ohio State',
            [event('KXNCAAFSPREAD', 'Ohio State Buckeyes vs Marshall')],
            [market(label='Marshall wins by over 27.5 points', floor_strike=27.5, strike_type='greater')])
        self.assertEqual((result.status, result.side), ('MATCHED', 'no'))

    def test_total_over_under_wrong_lines_and_integer_push(self):
        events = [event('KXNFLTOTAL')]
        markets = [market(label='Over 40.5 points', floor_strike=40.5, strike_type='greater')]
        for side, expected in [('Over', 'yes'), ('Under 40.5', 'no')]:
            result = self.match('Falcons vs Packers: O/U 40.5', side, events, markets)
            self.assertEqual((result.status, result.side), ('MATCHED', expected))
        for title, side in [('Falcons vs Packers: O/U 41.5', 'Over'),
                            ('Falcons vs Packers: O/U 40.5', 'Over 42.5')]:
            self.assertEqual(self.match(title, side, events, markets).status, 'LINE_NOT_FOUND')
        self.assertEqual(self.match('Falcons vs Packers: O/U 40', 'Under', events, markets).status,
                         'UNSUPPORTED_MARKET_SCOPE')

    def test_tennis_initials_are_scoped_to_tennis(self):
        result = self.match('ATP: N. Djokovic vs R. Nadal', 'N. Djokovic',
            [event('KXATPGAME', 'Novak Djokovic vs Rafael Nadal')], [market(label='Novak Djokovic')])
        self.assertEqual(result.status, 'MATCHED')
        result = self.match('ATP: Novak Djokovic vs Rafael Nadal', 'Novak Djokovic',
            [event('KXATPGAME', 'Novak Djokovic vs Rafael Nadal')], [market(label='Other Djokovic')])
        self.assertEqual(result.status, 'CONTRACT_NOT_FOUND')

    def test_tennis_names_must_map_to_distinct_participants(self):
        result = self.match('ATP: N. Djokovic vs Novak Djokovic', 'N. Djokovic',
            [event('KXATPGAME', 'Novak Djokovic vs Rafael Nadal')], [market(label='Novak Djokovic')])
        self.assertEqual(result.status, 'EVENT_PARTICIPANTS_MISMATCH')

    def test_esports_spread_and_total_maps(self):
        for title, side, series, label, line, expected in (
            ('Counter-Strike: Spread: magic (-1.5)', 'Example Team', 'KXCS2SPREAD',
             'magic wins by over 1.5 maps', 1.5, 'no'),
            ('Dota 2: Team A vs Team B: O/U 2.5', 'Over', 'KXDOTA2TOTALMAPS',
             'Over 2.5 maps', 2.5, 'yes'),
        ):
            names = 'magic vs Example Team' if series == 'KXCS2SPREAD' else 'Team A vs Team B'
            result = self.match(title, side, [event(series, names)],
                [market(label=label, floor_strike=line, strike_type='greater')])
            self.assertEqual((result.status, result.side), ('MATCHED', expected))

    def test_reported_full_game_total_regressions_over_and_under(self):
        cases = (
            ('Wake Forest vs. Louisville: O/U 74.5', 'KXNCAAFTOTAL',
             'Wake Forest vs Louisville: Total Points', '74.5'),
            ('England vs. Spain: O/U 2.5', 'KXUEFANLTOTAL',
             'England vs Spain: Total Goals', '2.5'),
            ('North Macedonia vs. Switzerland: O/U 2.5', 'KXUEFANLTOTAL',
             'North Macedonia vs Switzerland: Total Goals', '2.5'),
            ('Falcons vs. Packers: O/U 40.5', 'KXNFLTOTAL',
             'ATL Falcons vs GB Packers: Total Points', '40.5'),
            ('Colorado vs. Baylor: O/U 35.5', 'KXNCAAFTOTAL',
             'Colorado vs Baylor: Total Points', '35.5'),
        )
        for alert_title, series, event_title, line_text in cases:
            event_ticker = f'{series}-26SEP26-TEST'
            line = float(line_text)
            event_row = event(series, event_title, event_ticker)
            market_row = market(f'{event_ticker}-{line_text}', label=f'Over {line_text}',
                                event_ticker=event_ticker, floor_strike=line,
                                strike_type='greater')
            for whale_side, expected in (('Over', 'yes'), ('Under', 'no')):
                with self.subTest(alert=alert_title, side=whale_side):
                    client = FakeClient([event_row], [market_row])
                    result = Matcher(client).match(parse_alert(message(alert_title, whale_side)))
                    self.assertEqual((result.status, result.side), ('MATCHED', expected))
                    self.assertIn(('recent_series', series), client.calls)

    def test_total_failure_reasons_are_stage_specific(self):
        alert = parse_alert(message('ATP: Player A vs Player B: O/U 22.5', 'Over'))
        result = Matcher(FakeClient([], [])).match(alert)
        self.assertEqual((result.status, result.reason),
                         ('NO_EVENTS_RETURNED', 'Configured series returned no recent events'))

        result = self.match('England vs Spain: O/U 2.5', 'Over', [], [])
        self.assertEqual((result.status, result.reason),
                         ('NO_EVENTS_RETURNED', 'Configured series returned no recent events'))

        event_row = event('KXUEFANLTOTAL', 'England vs Spain: Total Goals', 'TOTAL-EVENT')
        wrong_line = market('WRONG-LINE', label='Over 3.5', event_ticker='TOTAL-EVENT',
                            floor_strike=3.5, strike_type='greater')
        result = self.match('England vs Spain: O/U 2.5', 'Over', [event_row], [wrong_line])
        self.assertEqual(result.status, 'LINE_NOT_FOUND')
        self.assertEqual(result.reason, 'Event found but exact total line 2.5 is missing')

        duplicates = [
            market('FIRST', label='Over 2.5', event_ticker='TOTAL-EVENT',
                   floor_strike=2.5, strike_type='greater'),
            market('SECOND', label='Over 2.5', event_ticker='TOTAL-EVENT',
                   floor_strike=2.5, strike_type='greater'),
        ]
        result = self.match('England vs Spain: O/U 2.5', 'Under', [event_row], duplicates)
        self.assertEqual((result.status, result.reason),
                         ('AMBIGUOUS_MATCH', 'Multiple exact contracts match the event'))

        closed = market('CLOSED', label='Over 2.5', event_ticker='TOTAL-EVENT',
                        floor_strike=2.5, strike_type='greater', status='closed')
        result = self.match('England vs Spain: O/U 2.5', 'Over', [event_row], [closed])
        self.assertEqual((result.status, result.reason),
                         ('MATCHED', 'Unique series, event, contract and side'))
        self.assertEqual(result.diagnostics['matched_contract_status'], 'closed')

    def test_total_participants_must_match_exactly(self):
        event_row = event('KXUEFANLTOTAL', 'England U21 vs Spain: Total Goals', 'TOTAL-EVENT')
        market_row = market('TOTAL', label='Over 2.5', event_ticker='TOTAL-EVENT',
                            floor_strike=2.5, strike_type='greater')
        result = self.match('England vs Spain: O/U 2.5', 'Over', [event_row], [market_row])
        self.assertEqual(result.status, 'EVENT_PARTICIPANTS_MISMATCH')

    def test_tennis_and_esports_formats(self):
        for title, side, series, names in [
            ('Buenos Aires 2: Player vs Example Opponent', 'Player', 'KXATPGAME', 'Player vs Example Opponent'),
            ('Counter-Strike: magic vs Example Team (BO3) - Winner', 'magic', 'KXCS2GAME', 'magic vs. Example Team'),
            ('Dota 2: Team A vs Team B (BO3)', 'Team B', 'KXDOTA2GAME', 'Team A vs. Team B'),
            ('League of Legends: Team A vs Team B', 'Team A', 'KXLOLGAME', 'Team A vs. Team B'),
            ('Valorant: Team A vs Team B', 'Team A', 'KXVALORANTGAME', 'Team A vs. Team B'),
        ]:
            with self.subTest(title=title):
                result = self.match(title, side, [event(series, names)], [market(label=side)])
                self.assertEqual(result.status, 'MATCHED')

    def test_partial_game_rejected_before_network(self):
        client = FakeClient([], [])
        for title in ('Counter-Strike: A vs B - Map 1 Winner',
                      'Counter-Strike: A vs B - Total Maps',
                      'Counter-Strike: A vs B: To Win Tournament',
                      'Counter-Strike: Map Winner: A vs B'):
            result = Matcher(client).match(parse_alert(message(title, 'A')))
            self.assertEqual(result.status, 'UNSUPPORTED_MARKET_SCOPE')
        self.assertEqual(client.calls, [])

    def test_closed_and_missing_winner_contracts_are_distinguished(self):
        closed = self.match('NFL: Falcons vs Packers', 'Falcons', [event()],
                            [market(status='closed')])
        self.assertEqual((closed.status, closed.reason),
                         ('MATCHED', 'Unique series, event, contract and side'))
        self.assertEqual(closed.diagnostics['matched_contract_status'], 'closed')
        for m in [market(label='ATL Falcons Academy'), market(label='Will Falcons win?')]:
            result = self.match('NFL: Falcons vs Packers', 'Falcons', [event()], [m])
            self.assertEqual((result.status, result.reason),
                             ('CONTRACT_NOT_FOUND', 'Event found but winner contract is missing'))

    def test_current_ncaaf_and_mls_name_regressions(self):
        cases = (
            ('Oklahoma State vs. West Virginia', 'Oklahoma State',
             event('KXNCAAFGAME', 'Oklahoma St. vs West Virginia', 'OKST-WVU'),
             market('OKST', label='Oklahoma St.', event_ticker='OKST-WVU'), 'yes'),
            ('Kansas State vs. Cincinnati', 'Cincinnati',
             event('KXNCAAFGAME', 'Kansas St. vs Cincinnati', 'KSU-CIN'),
             market('CIN', label='Cincinnati', event_ticker='KSU-CIN'), 'yes'),
            ('Spread: Cincinnati (-2.5)', 'Kansas State',
             event('KXNCAAFSPREAD', 'Kansas St. vs Cincinnati: Spread', 'KSU-CIN-SPREAD'),
             market('CIN-SPREAD', label='Cincinnati wins by over 2.5 points',
                    event_ticker='KSU-CIN-SPREAD', floor_strike=2.5,
                    strike_type='greater'), 'no'),
            ('Los Angeles Galaxy vs. Colorado Rapids SC: O/U 2.5', 'Under',
             event('KXMLSTOTAL', 'LA Galaxy vs Colorado Rapids: Total Goals', 'LAG-COL-TOTAL'),
             market('LAG-COL-25', label='Over 2.5 goals scored',
                    event_ticker='LAG-COL-TOTAL', floor_strike=2.5,
                    strike_type='greater'), 'no'),
            ('Austin FC vs. San Diego FC: O/U 0.5', 'Over',
             event('KXMLSTOTAL', 'Austin vs San Diego FC: Total Goals', 'ATX-SD-TOTAL'),
             market('ATX-SD-05', label='Over 0.5 goals scored',
                    event_ticker='ATX-SD-TOTAL', floor_strike=.5,
                    strike_type='greater'), 'yes'),
        )
        for title, side, event_row, market_row, expected in cases:
            with self.subTest(title=title):
                client = FakeClient([event_row], [market_row])
                result = Matcher(client).match(parse_alert(message(title, side)))
                self.assertEqual((result.status, result.side), ('MATCHED', expected))
                self.assertIn(('recent_series', event_row['series_ticker']), client.calls)

    def test_exact_event_context_reuses_sport_family(self):
        events = [
            event('KXNCAAFSPREAD', 'Kansas St. vs Cincinnati: Spread',
                  'KXNCAAFSPREAD-26SEP27KSCIN'),
            event('KXNCAAFGAME', 'Kansas St. vs Cincinnati',
                  'KXNCAAFGAME-26SEP27KSCIN'),
            event('KXNCAAFGAME', 'Kansas St. vs Cincinnati',
                  'KXNCAAFGAME-26OCT10KSCIN'),
        ]
        markets = [
            market('SPREAD', label='Cincinnati wins by over 2.5 points',
                   event_ticker='KXNCAAFSPREAD-26SEP27KSCIN', floor_strike=2.5,
                   strike_type='greater'),
            market('WINNER', label='Cincinnati',
                   event_ticker='KXNCAAFGAME-26SEP27KSCIN'),
            market('FUTURE-WINNER', label='Cincinnati',
                   event_ticker='KXNCAAFGAME-26OCT10KSCIN'),
        ]
        client = FakeClient(events, markets)
        matcher = Matcher(client)
        self.assertEqual(matcher.match(parse_alert(
            message('Spread: Cincinnati (-2.5)', 'Kansas State'))).status, 'MATCHED')
        client.calls.clear()
        result = matcher.match(parse_alert(
            message('Kansas State vs Cincinnati', 'Cincinnati')))
        self.assertEqual(result.status, 'MATCHED')
        self.assertEqual(result.event['event_ticker'], 'KXNCAAFGAME-26SEP27KSCIN')
        self.assertEqual(result.diagnostics['event_context_suffixes'], ['-26SEP27KSCIN'])
        recent_calls = [call for call in client.calls if call[0] == 'recent_series']
        self.assertEqual(recent_calls, [('recent_series', 'KXNCAAFGAME')])

    def test_duplicate_contracts_ambiguous(self):
        self.assertEqual(self.match('NFL: Falcons vs Packers', 'Falcons', [event()],
                                    [market(), market('SECOND')]).status, 'AMBIGUOUS_MATCH')

    def test_single_team_spread_reuses_unique_event_context(self):
        snapshot = json.loads((Path(__file__).parent / 'fixtures' /
                               'matching_coverage_snapshot.json').read_text(encoding='utf-8'))
        events = [row['event'] for row in snapshot]
        markets = [market_row for row in snapshot for market_row in row['markets']]
        client = FakeClient(events, markets)
        matcher = Matcher(client)
        winner = matcher.match(parse_alert(message('Jets vs. Lions', 'Jets')))
        self.assertEqual(winner.status, 'MATCHED')
        client.calls.clear()
        spread = matcher.match(parse_alert(message('Spread: Lions (-14.5)', 'Jets')))
        self.assertEqual(spread.status, 'MATCHED')
        self.assertEqual(spread.event['event_ticker'], 'KXNFLSPREAD-26SEP27NYJDET')
        self.assertEqual(spread.diagnostics['sport_guess_source'], 'event_context')
        self.assertEqual(spread.diagnostics['event_context_suffixes'], ['-26SEP27NYJDET'])

    def test_overnight_matching_coverage_from_public_snapshot(self):
        snapshot = json.loads((Path(__file__).parent / 'fixtures' /
                               'matching_coverage_snapshot.json').read_text(encoding='utf-8'))
        events = [row['event'] for row in snapshot]
        markets = [market_row for row in snapshot for market_row in row['markets']]
        cases = (
            ('Rams vs. Broncos', 'Rams', 'KXNFLGAME', 'yes'),
            ('Jets vs. Lions', 'Jets', 'KXNFLGAME', 'yes'),
            ('Patriots vs. Jaguars', 'Patriots', 'KXNFLGAME', 'yes'),
            ('Jets vs. Lions: O/U 46.5', 'Under', 'KXNFLTOTAL', 'no'),
            ('Chargers vs. Bills: O/U 47.5', 'Over', 'KXNFLTOTAL', 'yes'),
            ('Spread: Lions (-14.5)', 'Jets', 'KXNFLSPREAD', 'no'),
            ('Spread: Bills (-7.5)', 'Chargers', 'KXNFLSPREAD', 'no'),
            ('Spread: Bengals (-4.5)', 'Steelers', 'KXNFLSPREAD', 'no'),
            ('Texas Rangers vs. Minnesota Twins', 'Texas Rangers', 'KXMLBGAME', 'yes'),
            ('Tampa Bay Rays vs. Philadelphia Phillies', 'Philadelphia Phillies',
             'KXMLBGAME', 'yes'),
            ('Counter-Strike: NIP vs magic (BO3) - 1win Private Club #1 Playoffs',
             'NIP', 'KXCS2GAME', 'yes'),
        )
        for title, selected, expected_series, expected_side in cases:
            with self.subTest(title=title):
                client = FakeClient(events, markets)
                result = Matcher(client).match(parse_alert(message(title, selected)))
                self.assertEqual((result.status, result.side), ('MATCHED', expected_side))
                self.assertEqual(result.event['series_ticker'], expected_series)
                self.assertEqual(result.diagnostics['candidate_series_checked'], [expected_series])

    def test_explicit_first_half_moneyline_and_team_total_scopes(self):
        snapshot = json.loads((Path(__file__).parent / 'fixtures' /
                               'matching_coverage_snapshot.json').read_text(encoding='utf-8'))
        events = [row['event'] for row in snapshot]
        markets = [market_row for row in snapshot for market_row in row['markets']]
        cases = (
            ('Chiefs vs. Dolphins: 1H Moneyline', 'Chiefs', 'first_half',
             'KXNFL1H', 'yes'),
            ('Jets Team Total: O/U 20.5', 'Under', 'team_total',
             'KXNFLTEAMTOTAL', 'no'),
        )
        for title, selected, scope, expected_series, expected_side in cases:
            with self.subTest(title=title):
                alert = parse_alert(message(title, selected))
                self.assertEqual(alert['market_scope'], scope)
                result = Matcher(FakeClient(events, markets)).match(alert)
                self.assertEqual((result.status, result.side), ('MATCHED', expected_side))
                self.assertEqual(result.event['series_ticker'], expected_series)

    def test_failure_codes_include_actionable_diagnostics(self):
        alert = parse_alert(message('Rams vs Broncos', 'Rams'))
        result = Matcher(FakeClient([], [])).match(alert)
        self.assertEqual(result.status, 'NO_EVENTS_RETURNED')
        self.assertEqual(result.diagnostics['sport_league_guess'], ['nfl'])
        self.assertEqual(result.diagnostics['candidate_series_checked'], ['KXNFLGAME'])
        self.assertIn('normalized_participants', result.diagnostics)

        candidate = event('KXNFLGAME', 'Rams Academy vs Denver', 'BAD-NAME')
        result = Matcher(FakeClient([candidate], [])).match(alert)
        self.assertEqual(result.status, 'EVENT_PARTICIPANTS_MISMATCH')
        self.assertEqual(result.diagnostics['closest_candidate_events'][0]['event_ticker'],
                         'BAD-NAME')

    def test_remaining_nfl_and_valorant_examples_with_full_diagnostics(self):
        cases = (
            ('Chargers vs. Bills', 'Chargers', 'KXNFLGAME',
             'Los Angeles C vs Buffalo', 'Los Angeles C', None),
            ('Bengals vs. Steelers', 'Steelers', 'KXNFLGAME',
             'Cincinnati vs Pittsburgh', 'Pittsburgh', None),
            ('Panthers vs. Browns', 'Panthers', 'KXNFLGAME',
             'Carolina vs Cleveland', 'Carolina', None),
            ('Patriots vs. Jaguars', 'Jaguars', 'KXNFLGAME',
             'New England vs Jacksonville', 'Jacksonville', None),
            ('Chargers vs. Bills: O/U 41.5', 'Over', 'KXNFLTOTAL',
             'LA Chargers vs BUF Bills: Total Points', 'Over 41.5', 41.5),
            ('Patriots vs. Jaguars: O/U 41.5', 'Under', 'KXNFLTOTAL',
             'NE Patriots vs JAC Jaguars: Total Points', 'Over 41.5', 41.5),
            ('Spread: Jaguars (-4.5)', 'Patriots', 'KXNFLSPREAD',
             'NE Patriots vs JAC Jaguars: Spread',
             'JAC Jaguars wins by over 4.5 points', 4.5),
            ('Spread: Bengals (-4.5)', 'Steelers', 'KXNFLSPREAD',
             'CIN Bengals vs PIT Steelers: Spread',
             'CIN Bengals wins by over 4.5 points', 4.5),
            ('Spread: Bills (-7.5)', 'Chargers', 'KXNFLSPREAD',
             'LA Chargers vs BUF Bills: Spread',
             'BUF Bills wins by over 7.5 points', 7.5),
            ('Valorant: G2 Esports vs Paper Rex (BO3) - VCT Champions Group C',
             'Paper Rex', 'KXVALORANTGAME', 'G2 Esports vs. Paper Rex',
             'Paper Rex', None),
        )
        for index, (title, selected, series, event_title, label, line) in enumerate(cases):
            with self.subTest(title=title):
                event_ticker = f'{series}-26SEP29-CASE{index}'
                extra = ({'floor_strike': line, 'strike_type': 'greater'}
                         if line is not None else {})
                alert = parse_alert(message(title, selected))
                result = Matcher(FakeClient(
                    [event(series, event_title, event_ticker)],
                    [market(f'{event_ticker}-CONTRACT', label, event_ticker, **extra)],
                )).match(alert)
                self.assertEqual(result.status, 'MATCHED')
                expected_league = 'valorant' if series == 'KXVALORANTGAME' else 'nfl'
                self.assertEqual(result.diagnostics['detected_league'], expected_league)
                self.assertEqual(result.diagnostics['series_results'][0]['events_returned'], 1)
                self.assertEqual(result.diagnostics['series_results'][0]['participant_matches'], 1)
                self.assertFalse(result.diagnostics['event_matched_contract_failed'])

    def test_exact_failure_taxonomy_and_rejection_trace(self):
        alert = parse_alert(message('NFL: Chargers vs Bills', 'Chargers'))
        no_events = Matcher(FakeClient([], [])).match(alert)
        self.assertEqual(no_events.status, 'NO_EVENTS_RETURNED')
        self.assertEqual(no_events.diagnostics['series_results'], [{
            'series_ticker': 'KXNFLGAME', 'events_returned': 0,
            'participant_matches': 0, 'date_mismatches': 0,
        }])

        wrong = event('KXNFLGAME', 'Buffalo vs New England', 'WRONG')
        mismatch = Matcher(FakeClient([wrong], [])).match(alert)
        self.assertEqual(mismatch.status, 'EVENT_PARTICIPANTS_MISMATCH')
        self.assertEqual(mismatch.diagnostics['event_rejections'][0]['reason'],
                         'participant identities do not match exactly')

        exact = event('KXNFLTOTAL', 'LA Chargers vs BUF Bills: Total Points', 'TOTAL')
        missing_line = Matcher(FakeClient([exact], [
            market('OTHER-LINE', 'Over 42.5', 'TOTAL', floor_strike=42.5,
                   strike_type='greater')
        ])).match(parse_alert(message('Chargers vs Bills: O/U 41.5', 'Over')))
        self.assertEqual(missing_line.status, 'LINE_NOT_FOUND')
        self.assertTrue(missing_line.diagnostics['event_matched_contract_failed'])
        self.assertEqual(missing_line.diagnostics['contracts_checked'], 1)

    def test_captured_public_payloads(self):
        snapshots = json.loads((Path(__file__).parent / 'fixtures/kalshi_public_snapshot.json').read_text())
        for e in snapshots:
            m = e['markets'][0]
            if e['series_ticker'] == 'KXNFLSPREAD':
                title, side, expected = 'NFL: Spread: CAR Panthers (-20.5)', 'CLE Browns', 'no'
            elif e['series_ticker'] == 'KXNFLTOTAL':
                title, side, expected = 'NFL: CAR Panthers vs CLE Browns: O/U 19.5', 'Under', 'no'
            else:
                title, side, expected = 'Counter-Strike: BakS eSports vs Leo Team (BO3)', 'Leo Team', 'yes'
            with self.subTest(series=e['series_ticker']):
                result = self.match(title, side, [e], e['markets'])
                self.assertEqual((result.status, result.side), ('MATCHED', expected))

