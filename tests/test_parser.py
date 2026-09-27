import unittest

from alert_parser import parse_alert
from names import same_name, sports_for_names
from samples import SAMPLES, message
from series_map import candidate_series


class ParserTests(unittest.TestCase):
    def test_existing_examples(self):
        for market, side, kind, team1, team2, line in SAMPLES:
            with self.subTest(market=market, side=side):
                alert = parse_alert(message(market, side))
                self.assertEqual((alert['market_type'], alert['team1'], alert['team2'], alert['line']),
                                 (kind, team1, team2, line))
                self.assertEqual(alert['amount'], 1250)
                self.assertEqual(alert['price'], .5)

    def test_prefixed_total_case_and_signed_roi(self):
        alert = parse_alert(message('NFL: Falcons VS. Packers: Over/Under 40.5', 'Over', roi=-12))
        self.assertEqual((alert['team1'], alert['team2'], alert['roi']), ('Falcons', 'Packers', -12))
        self.assertEqual(candidate_series(alert), ['KXNFLTOTAL'])

    def test_decimal_amount(self):
        alert = parse_alert(message('A vs B', 'A').replace('$1,250', '$1,250.50'))
        self.assertEqual(alert['amount'], 1250.5)

    def test_invalid(self):
        for text in (None, '', 'bad\nmessage', message('A vs B', 'A', '1.00'),
                     message('A vs B', 'A', '0.00'), message('A vs B', 'A').replace('75%', '175%'),
                     message('A vs B', 'A').replace('1,250', ',,,')):
            self.assertIsNone(parse_alert(text))

    def test_route_esports_and_tennis(self):
        for title, side, series in (
            ('Counter-Strike: magic vs Example Team (BO3)', 'magic', ['KXCS2GAME']),
            ('Buenos Aires 2: Player vs Example Opponent', 'Player', ['KXATPGAME', 'KXWTAGAME']),
        ):
            self.assertEqual(candidate_series(parse_alert(message(title, side))), series)

    def test_professional_league_aliases_narrow_series(self):
        cases = (
            ('Rams vs Broncos', 'Rams', ['KXNFLGAME']),
            ('Texas Rangers vs Minnesota Twins', 'Texas Rangers', ['KXMLBGAME']),
            ('WNBA: Aces vs Liberty', 'Aces', ['KXWNBAGAME']),
            ('NHL: Bruins vs Sabres', 'Bruins', ['KXNHLGAME']),
        )
        for title, side, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(candidate_series(parse_alert(message(title, side))), expected)
        self.assertTrue(same_name('Bills', 'Buffalo Bills'))
        self.assertTrue(same_name('Rams', 'Los Angeles R'))
        self.assertTrue(same_name('Chargers', 'Los Angeles Chargers'))
        self.assertTrue(same_name('49ers', 'San Francisco 49ers'))
        self.assertTrue(same_name('Rays', 'Tampa Bay'))
        self.assertTrue(same_name('Phillies', 'Philadelphia'))
        self.assertEqual(sports_for_names('Jets', 'Lions'), ('nfl',))

    def test_mls_series_cover_winner_spread_and_total(self):
        self.assertIn('KXMLSGAME', candidate_series(
            parse_alert(message('MLS: Austin FC vs San Diego FC', 'Austin FC'))))
        self.assertIn('KXMLSSPREAD', candidate_series(
            parse_alert(message('MLS: Spread: Austin FC (-1.5)', 'San Diego FC'))))
        self.assertEqual(candidate_series(parse_alert(
            message('MLS: Austin FC vs San Diego FC: O/U 2.5', 'Over'))), ['KXMLSTOTAL'])

    def test_dated_yes_no_winner_propositions(self):
        for side in ('Yes', 'No'):
            with self.subTest(side=side):
                alert = parse_alert(message('Will England win on 2026-09-26?', side))
                self.assertEqual(alert['market_type'], 'game_winner')
                self.assertEqual(alert['team1'], 'England')
                self.assertIsNone(alert['team2'])
                self.assertEqual(alert['market_date'], '2026-09-26')
                self.assertTrue(alert['yes_no_proposition'])
                self.assertEqual(alert['side'], side)

    def test_partial_scope_parsing_and_series_are_explicit(self):
        first_half = parse_alert(message('Chiefs vs. Dolphins: 1H Moneyline', 'Chiefs'))
        self.assertEqual((first_half['market_type'], first_half['market_scope']),
                         ('game_winner', 'first_half'))
        self.assertEqual(candidate_series(first_half), ['KXNFL1H'])

        team_total = parse_alert(message('Jets Team Total: O/U 20.5', 'Under'))
        self.assertEqual((team_total['market_type'], team_total['market_scope'],
                          team_total['team1'], team_total['line']),
                         ('total', 'team_total', 'Jets', 20.5))
        self.assertEqual(candidate_series(team_total),
                         ['KXNFLTEAMTOTAL', 'KXNHLTEAMTOTAL'])

    def test_invalid_or_non_winner_questions_are_not_propositions(self):
        for title in ('Will England win on 2026-02-30?',
                      'Will England score on 2026-09-26?',
                      'Will England win?'):
            with self.subTest(title=title):
                alert = parse_alert(message(title, 'Yes'))
                self.assertEqual(alert['market_type'], 'unknown')
                self.assertFalse(alert['yes_no_proposition'])
