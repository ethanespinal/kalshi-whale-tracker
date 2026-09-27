from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from dashboard_ui import (TERMINAL_CSS, activity_html, compact_grid_html,
                          countdown_span, header_html, metrics_html, open_bets_html,
                          format_performance_value, resolution_countdown,
                          terminal_component_document, tone,
                          whales_html)


class DashboardUiTests(unittest.TestCase):
    def test_terminal_theme_uses_semantic_colors_and_compact_grid(self):
        self.assertIn('--tui-bg: #080a0b', TERMINAL_CSS)
        self.assertIn('--tui-cyan: #62c7c5', TERMINAL_CSS)
        self.assertIn('.tui-grid', TERMINAL_CSS)
        self.assertIn('@media (max-width: 850px)', TERMINAL_CSS)

    def test_header_and_metrics_escape_external_text(self):
        header = header_html(True, '<script>bad()</script>', 'session&one',
                             [('heartbeat', '12:00')])
        metrics = metrics_html([('P/L', '$4.00', 'tui-positive', '<unsafe>')])
        self.assertIn('ONLINE', header)
        self.assertIn('&lt;script&gt;bad()&lt;/script&gt;', header)
        self.assertNotIn('<script>', header)
        self.assertIn('session&amp;one', header)
        self.assertIn('&lt;unsafe&gt;', metrics)

    def test_activity_table_is_compact_and_colors_results(self):
        rows = [{
            'time': 1_700_000_000.0, 'trader': 'Alpha',
            'original_market': 'Falcons < Packers', 'whale_side': 'Over',
            'status': 'WIN', 'slippage': Decimal('.02'),
            'result_p_l': Decimal('12.50'),
        }, {
            'time': 1_700_000_001.0, 'trader': 'Beta',
            'original_market': 'Skipped market', 'whale_side': 'No',
            'status': 'SKIPPED', 'slippage': None,
            'result_p_l': 'Exact line not found',
        }]
        html = activity_html(rows)
        self.assertIn('RECENT ACTIVITY', html)
        self.assertIn('PLACED TRADES ONLY', html)
        self.assertIn('Falcons &lt; Packers', html)
        self.assertIn('tui-positive">WIN', html)
        self.assertIn('tui-warning">SKIPPED', html)
        self.assertIn('$12.50', html)
        self.assertIn('2.0¢', html)
        self.assertNotIn('Exact line not found', html)

    def test_whale_panel_has_requested_summary_columns(self):
        html = whales_html([{
            'Trader': 'Whale One', 'Trades': 8, 'Wins': 5, 'Losses': 3,
            'Net P/L': Decimal('44.25'), 'ROI': Decimal('.22125'),
        }])
        for label in ('Trader', 'Settled', 'W-L', 'Net P/L', 'ROI'):
            self.assertIn(label, html)
        self.assertIn('5-3', html)
        self.assertIn('$44.25', html)
        self.assertIn('22.1%', html)

    def test_compact_grid_and_tone_are_presentation_only(self):
        html = compact_grid_html([], [])
        self.assertIn('tui-grid', html)
        self.assertEqual(tone('OFFLINE'), 'tui-negative')
        self.assertEqual(tone('SHADOW_ONLY'), 'tui-warning')
        self.assertEqual(tone(Decimal('-1'), pnl=True), 'tui-negative')
        self.assertEqual(tone(Decimal('1'), pnl=True), 'tui-positive')

    def test_resolution_countdown_formats_and_never_goes_negative(self):
        now = datetime(2026, 9, 26, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(resolution_countdown(
            (now + timedelta(hours=1, minutes=5, seconds=42)).isoformat(), now),
            '01:05:42')
        self.assertEqual(resolution_countdown(
            (now + timedelta(minutes=5, seconds=2)).isoformat(), now),
            '00:05:02')
        self.assertEqual(resolution_countdown(
            (now + timedelta(hours=27, minutes=14, seconds=8)).isoformat(), now),
            '27:14:08')
        self.assertEqual(resolution_countdown(
            (now - timedelta(seconds=1)).isoformat(), now),
            'AWAITING SETTLEMENT')
        self.assertEqual(resolution_countdown('', now), 'UNKNOWN')

    def test_client_countdown_ticks_each_second_and_cleans_up(self):
        target = '2026-09-28T03:14:08Z'
        span = countdown_span(target)
        document = terminal_component_document(span)
        self.assertIn(f'data-resolution-countdown="{target}"', document)
        self.assertIn("window.setInterval(tick, 1000)", document)
        self.assertIn("window.clearInterval(timerId)", document)
        self.assertIn("window.addEventListener('pagehide', stop", document)
        self.assertIn("node.textContent = 'AWAITING SETTLEMENT'", document)
        self.assertIn('Math.floor(remaining / 3600)', document)

    def test_live_and_open_countdowns_are_grouped_with_trade_details(self):
        target = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        activity = activity_html([{
            'time': 1_700_000_000.0, 'trader': 'Alpha',
            'original_market': 'Falcons vs Packers', 'whale_side': 'Over',
            'status': 'OPEN', 'slippage': Decimal('.01'), 'result_p_l': None,
            'effective_resolution_time': target,
        }])
        self.assertLess(activity.index('T−Resolve'), activity.index('Status'))
        self.assertIn('data-resolution-countdown', activity)

        open_bets = open_bets_html([{
            'time_opened': 1_700_000_000.0, 'trader': 'Alpha',
            'original_market': 'Falcons vs Packers', 'side': 'Over',
            'direction': 'YES', 'expected_resolution_time': target,
            'our_entry': Decimal('.51'), 'slippage': Decimal('.01'),
            'stake': Decimal('25'), 'contracts': Decimal('49.02'),
        }])
        self.assertLess(open_bets.index('T−Resolve'), open_bets.index('Status'))
        self.assertIn('Over / YES', open_bets)
        self.assertIn('data-resolution-countdown', open_bets)

    def test_all_activity_has_distinct_audit_title_and_legend(self):
        html = activity_html([], title='ALL ACTIVITY', note='ALL PROCESSED ALERTS')
        self.assertIn('ALL ACTIVITY', html)
        self.assertIn('ALL PROCESSED ALERTS', html)
        self.assertNotIn('PLACED TRADES ONLY', html)

    def test_finished_activity_displays_closed_in_resolution_column(self):
        html = activity_html([{
            'opened_time': 1_700_000_000.0, 'trader': 'Alpha',
            'original_market': 'Finished market', 'whale_side': 'Yes',
            'status': 'WIN', 'slippage': Decimal('0'),
            'result_p_l': Decimal('10'),
            'effective_resolution_time': '2026-09-27T12:00:00Z',
        }])
        self.assertIn('CLOSED', html)
        self.assertNotIn('data-resolution-countdown', html)

    def test_performance_formatter_converts_dollars_to_configured_units(self):
        self.assertEqual(format_performance_value(25, 'u', 25), '1.00u')
        self.assertEqual(format_performance_value(50, 'u', 25, signed=True), '+2.00u')
        self.assertEqual(format_performance_value(-12.50, 'u', 25, signed=True), '-0.50u')
        self.assertEqual(format_performance_value(100, 'u', 25), '4.00u')
        self.assertEqual(format_performance_value(25, 'units', 10), '2.50u')
        self.assertEqual(format_performance_value(25, '$', 10), '$25.00')

    def test_compact_panels_render_performance_in_units(self):
        activity = activity_html([{
            'time': 1_700_000_000.0, 'trader': 'Alpha', 'original_market': 'Market',
            'whale_side': 'Yes', 'status': 'WIN', 'slippage': Decimal('0'),
            'result_p_l': Decimal('12.50'),
        }], display_mode='u', fixed_stake_dollars=25)
        self.assertIn('+0.50u', activity)

        open_bets = open_bets_html([{
            'time_opened': 1_700_000_000.0, 'trader': 'Alpha',
            'original_market': 'Market', 'side': 'Yes', 'direction': 'YES',
            'expected_resolution_time': None, 'our_entry': Decimal('.50'),
            'slippage': Decimal('0'), 'stake': Decimal('25'), 'contracts': Decimal('50'),
        }], display_mode='u', fixed_stake_dollars=25)
        self.assertIn('1.00u', open_bets)

        whales = whales_html([{
            'Trader': 'Alpha', 'Trades': 2, 'Wins': 1, 'Losses': 1,
            'Net P/L': Decimal('50'), 'ROI': Decimal('.50'),
        }], display_mode='u', fixed_stake_dollars=25)
        self.assertIn('+2.00u', whales)


if __name__ == '__main__':
    unittest.main()
