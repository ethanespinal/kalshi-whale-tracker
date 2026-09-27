"""Read-only local Streamlit monitor for the paper-trading backend."""
from datetime import date, datetime, time as date_time, timedelta, timezone
from decimal import Decimal
import sqlite3

import streamlit as st
import streamlit.components.v1 as components

from config import Settings
from dashboard_data import (DashboardFilters, backend_is_online, dashboard_metrics,
                            comparison_rows, filter_options, grouped_rows, query_skipped_alerts,
                            conflicting_signal_count,
                            open_bet_rows, query_trades, read_operational_state, recent_activity,
                            placed_trade_activity, realized_positions, realized_summary,
                            recovery_summary, sort_activity_rows, trade_activity, whale_rows)
from dashboard_ui import (TERMINAL_CSS, activity_html, compact_grid_html, header_html,
                          format_performance_value, metrics_html, open_bets_html,
                          terminal_component_document, tone)


settings = Settings()
st.set_page_config(page_title='Kalshi Paper Terminal', page_icon='⌁', layout='wide')
st.html(TERMINAL_CSS)


def money(value):
    return 'n/a' if value is None else f'${Decimal(value):,.2f}'


def percent(value):
    return 'n/a' if value is None else f'{Decimal(value) * 100:.1f}%'


def price(value):
    return 'n/a' if value is None else f'{Decimal(value) * 100:.2f}¢'


def local_time(timestamp):
    if timestamp is None:
        return 'never'
    return datetime.fromtimestamp(timestamp, timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')


def display_rows(rows, display_mode='$', fixed_stake_dollars=25):
    """Format copies for Streamlit's detailed research tables."""
    output = []
    for row in rows:
        result = dict(row)
        if isinstance(result.get('time'), (int, float)):
            result['time'] = local_time(result['time'])
        if isinstance(result.get('time_opened'), (int, float)):
            result['time_opened'] = local_time(result['time_opened'])
        if isinstance(result.get('settled_time'), (int, float)):
            result['settled_time'] = local_time(result['settled_time'])
        for key in ('whale_price', 'our_entry_price', 'whale_entry', 'our_entry', 'slippage',
                    'entry', 'exit/settlement value'):
            if key in result:
                result[key] = price(result[key])
        performance_keys = ('stake', 'result_p_l', 'estimated_fee', 'total_paper_cost',
                            'open_exposure_contribution', 'Total stake', 'Net P/L',
                            'Stake', 'Fees', 'fees', 'gross_p_l', 'net_p_l')
        signed_keys = ('result_p_l', 'Net P/L', 'gross_p_l', 'net_p_l')
        for key in performance_keys:
            if key in result and isinstance(result[key], (int, float, Decimal)):
                result[key] = format_performance_value(
                    result[key], display_mode, fixed_stake_dollars,
                    signed=key in signed_keys)
        for key in ('Win rate', 'ROI'):
            if key in result:
                result[key] = percent(result[key])
        for key in ('whale_win_rate', 'whale_roi'):
            if key in result and result[key] is not None:
                result[key] = f'{Decimal(result[key]):.1f}%'
        if isinstance(result.get('contracts'), (int, float, Decimal)):
            result['contracts'] = f"{Decimal(str(result['contracts'])):.2f}"
        result.pop('_record', None)
        result.pop('hours_to_close', None)
        output.append(result)
    return output


try:
    options = filter_options(settings.database)
except (FileNotFoundError, OSError, sqlite3.Error):
    st.error('Paper database is unavailable or needs migration. Start the backend with `python telegram_listener.py`.')
    st.stop()

with st.sidebar:
    st.header(':: filters')
    # Historical data remains visible across deployments unless the operator
    # deliberately narrows the durable ledger to one strategy version.
    version_labels = ['All versions', f'Current strategy version ({settings.strategy_version})']
    version_labels += [value for value in options['strategy_versions']
                       if value != settings.strategy_version]
    version_choice = st.selectbox('Strategy version', version_labels)
    if version_choice == 'All versions':
        strategy_version = None
    elif version_choice.startswith('Current strategy version'):
        strategy_version = settings.strategy_version
    else:
        strategy_version = version_choice
    session_choice = st.selectbox('Session', ['All sessions'] + options['sessions'])
    mode_choice = st.selectbox(
        'Strategy mode', ['All strategy modes'] + options['strategy_modes'])
    portfolio_choice = st.segmented_control(
        'Research portfolio', ['Paper trades', 'All shadow signals', 'All qualifying'],
        default='Paper trades')
    recovery_choice = st.segmented_control(
        'Alert origin', ['All', 'Normal', 'Recovered'], default='All')
    use_dates = st.checkbox('Limit entry date range')
    date_range = st.date_input('Date range', value=(date.today() - timedelta(days=7), date.today()),
                               disabled=not use_dates)

    st.header(':: display')
    lcd_preview = st.toggle('LCD preview mode', value=False)
    lcd_size = st.selectbox('Viewport', ['800 × 480', '1024 × 600'],
                            disabled=not lcd_preview)
    st.caption('READ ONLY // AUTO REFRESH 5S')

session_id = None if session_choice == 'All sessions' else session_choice
strategy_mode_filter = None if mode_choice == 'All strategy modes' else mode_choice
start_ts = end_ts = None
if use_dates and isinstance(date_range, (tuple, list)) and len(date_range) == 2:
    local_zone = datetime.now().astimezone().tzinfo
    start_ts = datetime.combine(date_range[0], date_time.min, local_zone).timestamp()
    end_ts = datetime.combine(date_range[1] + timedelta(days=1), date_time.min, local_zone).timestamp()
filters = DashboardFilters(
    strategy_version=strategy_version, session_id=session_id,
    start_ts=start_ts, end_ts=end_ts, strategy_mode=strategy_mode_filter,
    recovery={'Normal': 'normal', 'Recovered': 'recovered'}.get(recovery_choice))
trade_mode = {'Paper trades': 'PAPER', 'All shadow signals': 'SHADOW_ONLY'}.get(portfolio_choice)

if lcd_preview:
    shell_width, shell_height = ((800, 480) if lcd_size.startswith('800') else (1024, 600))
else:
    shell_width, shell_height = 'stretch', 'content'

fixed_stake_dollars = Decimal(settings.fixed_stake) / Decimal(10_000)
display_spacer, display_control = st.columns([8, 2], vertical_alignment='center')
with display_control:
    display_mode = st.segmented_control(
        'DISPLAY', ['$', 'u'], default='$', key='dashboard_display_mode')


@st.fragment(run_every='5s')
def live_dashboard(active_filters, active_trade_mode, viewport_width, viewport_height,
                   active_display_mode):
    try:
        operational = read_operational_state(settings.database)
        recovery = recovery_summary(settings.database)
        all_trades = query_trades(settings.database, active_filters)
        trades = [row for row in all_trades
                  if active_trade_mode is None or row['trade_mode'] == active_trade_mode]
    except (FileNotFoundError, sqlite3.Error) as exc:
        st.error(f'Dashboard read failed: {exc}')
        return

    status = operational['backend_status']
    online = backend_is_online(status, offline_after=settings.backend_offline_after)
    strategy = status.get('strategy_version', 'unknown') if status else settings.strategy_version
    session = status.get('session_id', 'unknown') if status else 'unknown'
    backend_run = status.get('backend_run_id', 'unknown') if status else 'unknown'
    strategy_mode = status.get('strategy_mode', settings.strategy_mode) if status else settings.strategy_mode
    timestamps = [
        ('heartbeat', local_time(status.get('last_heartbeat')) if status else 'never'),
        ('telegram', local_time(status.get('last_telegram_alert')) if status else 'never'),
        ('kalshi', local_time(status.get('last_kalshi_lookup')) if status else 'never'),
        ('settlement', local_time(status.get('last_settlement_refresh')) if status else 'never'),
        ('backend run', backend_run),
        ('mode', strategy_mode),
    ]

    local_zone = datetime.now().astimezone().tzinfo
    day_start = datetime.combine(date.today(), date_time.min, local_zone).timestamp()
    day_end = datetime.combine(date.today() + timedelta(days=1),
                               date_time.min, local_zone).timestamp()
    metrics = dashboard_metrics(trades, operational['starting_bankroll'], day_start,
                                operational['realized_pnl'], day_end)
    today_summary = realized_summary(trades, day_start, day_end)
    settled_today = realized_positions(trades, day_start, day_end)
    open_exposure = operational['open_exposure'] / 10000
    max_exposure = settings.max_exposure / 10000
    performance = lambda value, signed=False: format_performance_value(
        value, active_display_mode, fixed_stake_dollars, signed=signed)
    metric_items = [
        ('Bankroll', money(metrics['realized_bankroll']), tone(metrics['net_p_l'], pnl=True),
         f"start {money(metrics['paper_bankroll'])}"),
        ('Current P/L', performance(metrics['net_p_l'], signed=True),
         tone(metrics['net_p_l'], pnl=True), 'selected portfolio'),
        ('Today P/L', performance(metrics['today_net_p_l'], signed=True),
         tone(metrics['today_net_p_l'], pnl=True),
         percent(metrics['today_roi'])),
        ('Exposure', f'{performance(open_exposure)} / {performance(max_exposure)}',
         'tui-warning' if open_exposure >= max_exposure else 'tui-accent', 'open / limit'),
        ('Open', str(operational['open_trades']), 'tui-accent', 'paper trades'),
        ('Settled', str(metrics['settled_trades']), 'tui-accent', 'realized positions'),
        ('W-L', f"{metrics['wins']}-{metrics['losses']}",
         tone(metrics['net_p_l'], pnl=True),
         f"P {metrics['pushes']} / V {metrics['voids']}"),
        ('Win rate', percent(metrics['win_rate']), 'tui-accent', 'selected portfolio'),
        ('Avg slippage', price(metrics['average_slippage']),
         tone(metrics['average_slippage'], slippage=True), 'entry vs whale'),
    ]

    # The top panel is deliberately restricted to actual PAPER ledger rows.
    placed_activity = placed_trade_activity(all_trades, 100)
    # The lower audit feed ignores the portfolio-mode selector so skipped and
    # shadow signals remain visible alongside placed trades.
    all_activity = recent_activity(settings.database, active_filters, 100)
    leaders = whale_rows(trades, 'P/L')

    with st.container(key='terminal_shell', width=viewport_width,
                      height=viewport_height, gap='small'):
        st.html(header_html(online, strategy, session, timestamps))
        st.html(metrics_html(metric_items))
        st.caption(
            'RECOVERY // '
            f"RECONSIDERED {recovery['historical_alerts_reconsidered']} | "
            f"MATCHED {recovery['newly_matched']} | "
            f"PAPER FILLS {recovery['recovered_paper_trades']} | "
            f"PRICE WORSE {recovery['price_worsened']} | "
            f"OPPORTUNITY GONE {recovery['opportunity_gone']} | "
            f"STILL UNMATCHED {recovery['still_unmatched']}")
        st.caption(
            f"POSITION CONFLICTS // BLOCKED {conflicting_signal_count(all_trades)}")
        recent_sort_col, recent_order_col = st.columns([2, 2])
        with recent_sort_col:
            recent_sort = st.selectbox(
                'RECENT ACTIVITY sort', ['Time opened', 'Trader', 'T-resolve'],
                key='recent_activity_sort')
        with recent_order_col:
            recent_order = st.segmented_control(
                'RECENT ACTIVITY order', ['Descending', 'Ascending'],
                default='Descending', key='recent_activity_order')
        placed_activity = sort_activity_rows(
            placed_activity, recent_sort, ascending=recent_order == 'Ascending')
        compact_height = max(330, 70 + max(min(len(placed_activity), 12), len(leaders)) * 22)
        components.html(
            terminal_component_document(compact_grid_html(
                placed_activity, leaders, active_display_mode, fixed_stake_dollars)),
            height=compact_height, scrolling=False)

        live_tab, open_tab, today_tab, whales_tab, types_tab, slippage_tab, history_tab, skipped_tab = st.tabs(
            ['Live', 'OPEN BETS', 'SETTLED TODAY', 'Whales', 'Market Types', 'Slippage',
             'History', 'Skipped Alerts'])

        with live_tab:
            all_sort_col, all_order_col = st.columns([2, 2])
            with all_sort_col:
                all_sort = st.selectbox(
                    'ALL ACTIVITY sort', ['Time opened', 'Trader', 'T-resolve'],
                    key='all_activity_sort')
            with all_order_col:
                all_order = st.segmented_control(
                    'ALL ACTIVITY order', ['Descending', 'Ascending'],
                    default='Descending', key='all_activity_order')
            all_activity = sort_activity_rows(
                all_activity, all_sort, ascending=all_order == 'Ascending')
            live_height = min(520, 72 + min(len(all_activity), 20) * 22)
            components.html(
                terminal_component_document(activity_html(
                    all_activity, limit=20, title='ALL ACTIVITY',
                    note='ALL PROCESSED ALERTS', display_mode=active_display_mode,
                    fixed_stake_dollars=fixed_stake_dollars)),
                height=live_height, scrolling=len(all_activity) > 20)
            st.markdown('### :: activity diagnostics')
            st.dataframe(display_rows(all_activity, active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)
        with open_tab:
            active_positions = open_bet_rows(all_trades)
            if active_positions:
                open_height = min(520, 54 + min(len(active_positions), 20) * 22)
                components.html(
                    terminal_component_document(open_bets_html(
                        active_positions, display_mode=active_display_mode,
                        fixed_stake_dollars=fixed_stake_dollars)),
                    height=open_height, scrolling=len(active_positions) > 20)
                st.markdown('### :: position diagnostics')
                st.dataframe(display_rows(
                    active_positions, active_display_mode, fixed_stake_dollars),
                    width='stretch', hide_index=True)
            else:
                st.info('NO ACTIVE PAPER POSITIONS FOR CURRENT FILTERS')
        with today_tab:
            today_metrics = [
                ('Settled today', str(today_summary['settled_trades']), 'tui-accent',
                 'realized today'),
                ('W-L', f"{today_summary['wins']}-{today_summary['losses']}",
                 tone(today_summary['net_p_l'], pnl=True),
                 f"P {today_summary['pushes']} / V {today_summary['voids']}"),
                ('Gross P/L', performance(today_summary['gross_p_l'], signed=True),
                 tone(today_summary['gross_p_l'], pnl=True), 'before fees'),
                ('Fees', performance(today_summary['total_fees']), 'tui-warning',
                 'entry + exit'),
                ('Net P/L', performance(today_summary['net_p_l'], signed=True),
                 tone(today_summary['net_p_l'], pnl=True), 'equals Today P/L'),
            ]
            st.html(metrics_html(today_metrics))
            if settled_today:
                st.dataframe(display_rows(settled_today, active_display_mode,
                                          fixed_stake_dollars),
                             width='stretch', hide_index=True)
            else:
                st.info('NO POSITIONS REALIZED TODAY FOR CURRENT FILTERS')
        with whales_tab:
            sort_by = st.selectbox('Sort whales by', ['P/L', 'ROI', 'Trade count', 'Win rate'])
            st.dataframe(display_rows(whale_rows(trades, sort_by), active_display_mode,
                                      fixed_stake_dollars),
                         width='stretch', hide_index=True)
        with types_tab:
            st.markdown('### :: strategy modes')
            st.dataframe(display_rows(grouped_rows(all_trades, 'strategy_mode'),
                                      active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)
            st.markdown('### :: market types')
            st.dataframe(display_rows(grouped_rows(all_trades, 'market_type'),
                                      active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)
            st.markdown('### :: market type / paper-shadow')
            st.dataframe(display_rows(grouped_rows(all_trades, 'market_type_trade_mode'),
                                      active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)
        with slippage_tab:
            st.markdown('### :: strict strategy vs positive-slippage research')
            st.dataframe(display_rows(comparison_rows(all_trades), active_display_mode,
                                      fixed_stake_dollars),
                         width='stretch', hide_index=True)
            st.markdown('### :: slippage buckets')
            st.dataframe(display_rows(grouped_rows(trades, 'slippage'), active_display_mode,
                                      fixed_stake_dollars),
                         width='stretch', hide_index=True)
            st.markdown('### :: whale win-rate buckets')
            st.dataframe(display_rows(grouped_rows(trades, 'whale_win_rate_bucket'),
                                      active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)
            st.markdown('### :: whale roi buckets')
            st.dataframe(display_rows(grouped_rows(trades, 'whale_roi_bucket'),
                                      active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)
            st.markdown('### :: hours-to-resolution buckets')
            st.dataframe(display_rows(grouped_rows(trades, 'hours_to_close_bucket'),
                                      active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)
        with history_tab:
            st.dataframe(display_rows(trade_activity(trades), active_display_mode,
                                      fixed_stake_dollars), width='stretch', hide_index=True)
        with skipped_tab:
            st.dataframe(display_rows(query_skipped_alerts(settings.database, active_filters),
                                      active_display_mode, fixed_stake_dollars),
                         width='stretch', hide_index=True)


live_dashboard(filters, trade_mode, shell_width, shell_height, display_mode)
