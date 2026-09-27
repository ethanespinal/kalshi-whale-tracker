"""Small, data-agnostic renderers for the terminal-style dashboard."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape


TERMINAL_CSS = r"""
<style>
:root {
  --tui-bg: #080a0b;
  --tui-panel: #0b0e0f;
  --tui-panel-alt: #101416;
  --tui-border: #3b4448;
  --tui-dim: #78858a;
  --tui-text: #d8dee4;
  --tui-bright: #f1f5f7;
  --tui-cyan: #62c7c5;
  --tui-green: #75d681;
  --tui-amber: #ddb45b;
  --tui-red: #ff6b6b;
}

html, body, [class*="css"], [data-testid="stAppViewContainer"],
[data-testid="stSidebar"], button, input, textarea, select {
  font-family: "Cascadia Mono", "JetBrains Mono", "SFMono-Regular", Consolas, monospace !important;
}

.stApp, [data-testid="stAppViewContainer"] { background: var(--tui-bg); color: var(--tui-text); }
[data-testid="stHeader"] { background: transparent; }
[data-testid="stSidebar"] { background: #0a0d0e; border-right: 1px solid var(--tui-border); }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
  color: var(--tui-cyan); font-size: .82rem; letter-spacing: .08em; text-transform: uppercase;
}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
[data-testid="stSidebar"] label { font-size: .75rem; }

.block-container { padding: 1rem 1.15rem 2rem; max-width: 1600px; }
div[data-testid="stVerticalBlock"] { gap: .55rem; }
.st-key-terminal_shell {
  background: var(--tui-bg);
  border: 1px solid var(--tui-border);
  border-radius: 0;
  padding: .55rem .65rem;
}
.st-key-terminal_shell > div { scrollbar-color: var(--tui-border) var(--tui-bg); }

button, [data-baseweb="select"] > div, [data-baseweb="input"] > div,
[data-testid="stDateInput"] > div, [data-testid="stNumberInput"] > div {
  border-radius: 0 !important; box-shadow: none !important;
}
button:hover { border-color: var(--tui-cyan) !important; color: var(--tui-cyan) !important; }
[data-testid="stCheckbox"], [data-testid="stToggle"] { font-size: .78rem; }
.st-key-dashboard_display_mode {
  border: 1px solid var(--tui-border); background: var(--tui-panel);
  padding: .2rem .4rem; margin-left: auto;
}
.st-key-dashboard_display_mode [data-testid="stWidgetLabel"] {
  color: var(--tui-cyan); font-size: .62rem; letter-spacing: .06em;
  text-transform: uppercase;
}
.st-key-dashboard_display_mode [data-baseweb="button-group"] { gap: 0 !important; }

.tui-topline {
  min-height: 30px; display: flex; align-items: center; flex-wrap: wrap; gap: .45rem 1rem;
  border: 1px solid var(--tui-border); padding: .32rem .55rem; background: var(--tui-panel);
  font-size: .78rem; line-height: 1.2;
}
.tui-brand { color: var(--tui-bright); font-weight: 700; letter-spacing: .05em; }
.tui-brand::before { content: "[ "; color: var(--tui-dim); }
.tui-brand::after { content: " ]"; color: var(--tui-dim); }
.tui-spacer { flex: 1 1 auto; }
.tui-label { color: var(--tui-cyan); text-transform: uppercase; }
.tui-status { font-weight: 700; }
.tui-status::before { content: "● "; }
.tui-subline {
  color: var(--tui-dim); padding: .25rem .15rem .1rem; font-size: .66rem;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}

.tui-metrics {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr));
  border-top: 1px solid var(--tui-border); border-left: 1px solid var(--tui-border);
  background: var(--tui-panel); margin: .25rem 0 .5rem;
}
.tui-metric {
  min-width: 0; padding: .34rem .46rem; border-right: 1px solid var(--tui-border);
  border-bottom: 1px solid var(--tui-border);
}
.tui-metric-label {
  color: var(--tui-cyan); font-size: .62rem; letter-spacing: .055em;
  text-transform: uppercase; white-space: nowrap;
}
.tui-metric-value {
  color: var(--tui-bright); font-size: .95rem; font-weight: 700;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.tui-metric-note { color: var(--tui-dim); font-size: .57rem; }

.tui-grid { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(300px, 1fr); gap: .5rem; }
.tui-panel { border: 1px solid var(--tui-border); background: var(--tui-panel); min-width: 0; }
.tui-panel-title {
  display: flex; align-items: center; gap: .45rem; color: var(--tui-bright);
  border-bottom: 1px solid var(--tui-border); padding: .25rem .45rem;
  font-size: .69rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase;
}
.tui-panel-title::before { content: "├─"; color: var(--tui-border); }
.tui-panel-title::after { content: "────────────────"; color: var(--tui-border); overflow: hidden; }
.tui-count { color: var(--tui-dim); font-weight: 400; }
.tui-panel-note {
  color: var(--tui-dim); background: var(--tui-panel-alt); padding: .14rem .45rem;
  border-bottom: 1px solid #1d2326; font-size: .57rem; letter-spacing: .04em;
  text-transform: uppercase;
}
.tui-table-wrap { overflow-x: auto; }
.tui-table { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: .67rem; }
.tui-table th {
  color: var(--tui-cyan); background: var(--tui-panel-alt); font-weight: 600;
  padding: .19rem .32rem; text-align: left; border-bottom: 1px solid var(--tui-border);
  text-transform: uppercase; white-space: nowrap;
}
.tui-table td {
  color: var(--tui-text); padding: .17rem .32rem; line-height: 1.22;
  border-bottom: 1px solid #1d2326; overflow: hidden; white-space: nowrap; text-overflow: ellipsis;
}
.tui-table tr:last-child td { border-bottom: 0; }
.tui-table tr:hover td { background: #111719; color: var(--tui-bright); }
.tui-empty { color: var(--tui-dim); font-size: .7rem; padding: .7rem; }
.tui-positive { color: var(--tui-green) !important; }
.tui-warning { color: var(--tui-amber) !important; }
.tui-negative { color: var(--tui-red) !important; }
.tui-accent { color: var(--tui-cyan) !important; }
.tui-muted { color: var(--tui-dim) !important; }
.tui-countdown {
  display: inline-block; min-width: 8ch; color: var(--tui-cyan) !important;
  font-size: .62rem; font-variant-numeric: tabular-nums; letter-spacing: .01em;
}
.tui-countdown-awaiting { color: var(--tui-amber) !important; }

[data-baseweb="tab-list"] {
  gap: 0 !important; border: 1px solid var(--tui-border); background: var(--tui-panel);
  padding: 0 !important;
}
[data-baseweb="tab"] {
  height: 29px !important; border-right: 1px solid var(--tui-border) !important;
  border-radius: 0 !important; padding: 0 .65rem !important; font-size: .67rem !important;
  letter-spacing: .035em; text-transform: uppercase;
}
[data-baseweb="tab"][aria-selected="true"] { color: var(--tui-cyan) !important; background: #101617; }
[data-baseweb="tab-highlight"] { background-color: var(--tui-cyan) !important; height: 1px !important; }
[data-testid="stDataFrame"] { border: 1px solid var(--tui-border); border-radius: 0 !important; }
[data-testid="stDataFrame"] * { font-size: .69rem !important; }
[data-testid="stAlert"] { border-radius: 0; border: 1px solid var(--tui-border); padding: .4rem .6rem; }
h1, h2, h3 { color: var(--tui-bright); letter-spacing: .03em; }
h2, h3 { font-size: .83rem !important; text-transform: uppercase; }
hr { border-color: var(--tui-border); }

@media (max-width: 850px) {
  .block-container { padding: .5rem; }
  .tui-grid { grid-template-columns: 1fr; }
  .tui-metrics { grid-template-columns: repeat(3, minmax(100px, 1fr)); }
  [data-baseweb="tab"] { padding: 0 .38rem !important; }
}
</style>
"""


def _decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value):
    return escape(str(value if value not in (None, '') else '—'))


def _money(value):
    number = _decimal(value)
    return '—' if number is None else f'${number:,.2f}'


def format_performance_value(value, display_mode='$', fixed_stake_dollars=25,
                             *, signed=False):
    """Format a stored dollar value as dollars or configured betting units."""
    number = _decimal(value)
    if number is None:
        return '—'
    if str(display_mode).lower() not in ('u', 'unit', 'units'):
        return _money(number)
    unit_size = _decimal(fixed_stake_dollars)
    if unit_size is None or unit_size <= 0:
        return '—'
    units = number / unit_size
    prefix = '+' if signed and units > 0 else ''
    return f'{prefix}{units:.2f}u'


def _percent(value):
    number = _decimal(value)
    return '—' if number is None else f'{number * 100:.1f}%'


def _price(value):
    number = _decimal(value)
    return '—' if number is None else f'{number * 100:.1f}¢'


def _clock(value):
    if not isinstance(value, (int, float)):
        return _text(value)
    return datetime.fromtimestamp(value, timezone.utc).astimezone().strftime('%H:%M:%S')


def resolution_countdown(value, now=None):
    """Format an ISO resolution timestamp as a nonnegative live countdown."""
    if not value:
        return 'UNKNOWN'
    try:
        target = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return 'UNKNOWN'
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = int((target.astimezone(timezone.utc) - current.astimezone(timezone.utc)).total_seconds())
    if seconds <= 0:
        return 'AWAITING SETTLEMENT'
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}'


def countdown_span(value):
    """Render one timestamp for the browser timer while retaining a safe fallback."""
    initial = resolution_countdown(value)
    if not value or initial == 'UNKNOWN':
        return '<span class="tui-muted">UNKNOWN</span>'
    css = 'tui-countdown'
    if initial == 'AWAITING SETTLEMENT':
        css += ' tui-countdown-awaiting'
    timestamp = escape(str(value), quote=True)
    return (f'<span class="{css}" data-resolution-countdown="{timestamp}">'
            f'{escape(initial)}</span>')


COUNTDOWN_SCRIPT = r"""
<script>
(() => {
  const selector = '[data-resolution-countdown]';
  const pad = (value) => String(value).padStart(2, '0');
  const tick = () => {
    document.querySelectorAll(selector).forEach((node) => {
      const target = Date.parse(node.dataset.resolutionCountdown);
      if (!Number.isFinite(target)) {
        node.textContent = 'UNKNOWN';
        node.classList.remove('tui-countdown-awaiting');
        return;
      }
      const remaining = Math.ceil((target - Date.now()) / 1000);
      if (remaining <= 0) {
        node.textContent = 'AWAITING SETTLEMENT';
        node.classList.add('tui-countdown-awaiting');
        return;
      }
      const hours = Math.floor(remaining / 3600);
      const minutes = Math.floor((remaining % 3600) / 60);
      const seconds = remaining % 60;
      node.textContent = `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
      node.classList.remove('tui-countdown-awaiting');
    });
  };
  tick();
  let timerId = document.querySelector(selector) ? window.setInterval(tick, 1000) : null;
  const stop = () => {
    if (timerId !== null) window.clearInterval(timerId);
    timerId = null;
  };
  window.addEventListener('pagehide', stop, {once: true});
  window.addEventListener('beforeunload', stop, {once: true});
})();
</script>
"""


def terminal_component_document(content):
    """Create an isolated terminal panel whose countdowns tick without Python reruns."""
    return (f'<!doctype html><html><head>{TERMINAL_CSS}'
            '<style>html,body{margin:0;background:#080a0b;color:#d8dee4;overflow-x:auto}'
            '.tui-grid{margin:0}.tui-panel{height:auto}</style></head>'
            f'<body>{content}{COUNTDOWN_SCRIPT}</body></html>')


def tone(value, *, pnl=False, slippage=False):
    """Return a semantic terminal color class without changing the displayed value."""
    if pnl:
        number = _decimal(value)
        if number is None or number == 0:
            return 'tui-muted'
        return 'tui-positive' if number > 0 else 'tui-negative'
    if slippage:
        number = _decimal(value)
        if number is None or number == 0:
            return 'tui-muted'
        return 'tui-warning' if number > 0 else 'tui-positive'
    status = str(value or '').upper()
    if any(word in status for word in ('LOSS', 'ERROR', 'OFFLINE', 'FAILED')):
        return 'tui-negative'
    if any(word in status for word in ('SKIP', 'SHADOW', 'DEFER', 'WARN', 'VOID')):
        return 'tui-warning'
    if any(word in status for word in ('WIN', 'ONLINE', 'FILLED', 'SETTLED')):
        return 'tui-positive'
    return 'tui-accent'


def header_html(online, strategy, session, timestamps):
    status = 'ONLINE' if online else 'OFFLINE'
    details = ' │ '.join(
        f'<span class="tui-label">{escape(label)}</span> {_text(value)}'
        for label, value in timestamps
    )
    return f'''<div class="tui-topline">
      <span class="tui-brand">KALSHI PAPER TERMINAL</span>
      <span class="tui-status {tone(status)}">{status}</span>
      <span class="tui-spacer"></span>
      <span><span class="tui-label">strategy</span> {_text(strategy)}</span>
      <span><span class="tui-label">session</span> {_text(session)}</span>
    </div><div class="tui-subline">{details}</div>'''


def metrics_html(items):
    cells = []
    for item in items:
        label, value = item[:2]
        value_tone = item[2] if len(item) > 2 else ''
        note = item[3] if len(item) > 3 else ''
        cells.append(
            f'<div class="tui-metric"><div class="tui-metric-label">{_text(label)}</div>'
            f'<div class="tui-metric-value {escape(value_tone)}">{_text(value)}</div>'
            f'<div class="tui-metric-note">{_text(note) if note else "&nbsp;"}</div></div>'
        )
    return '<div class="tui-metrics">' + ''.join(cells) + '</div>'


def _table_html(title, headers, rows, widths=None, note=''):
    widths = widths or ['auto'] * len(headers)
    columns = ''.join(f'<col style="width:{escape(str(width))}">' for width in widths)
    head = ''.join(f'<th>{escape(header)}</th>' for header in headers)
    if rows:
        body = ''.join('<tr>' + ''.join(
            f'<td class="{escape(cell[1])}">{cell[0]}</td>' if isinstance(cell, tuple)
            else f'<td>{cell}</td>' for cell in row) + '</tr>' for row in rows)
        content = f'<div class="tui-table-wrap"><table class="tui-table"><colgroup>{columns}</colgroup>' \
                  f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
    else:
        content = '<div class="tui-empty">NO RECORDS FOR CURRENT FILTERS</div>'
    note_html = f'<div class="tui-panel-note">{escape(note)}</div>' if note else ''
    return (f'<section class="tui-panel"><div class="tui-panel-title">{escape(title)} '
            f'<span class="tui-count">[{len(rows):02d}]</span></div>{note_html}'
            f'{content}</section>')


def activity_html(rows, limit=12, title='RECENT ACTIVITY', note='PLACED TRADES ONLY',
                  display_mode='$', fixed_stake_dollars=25):
    body = []
    for row in rows[:limit]:
        pnl = row.get('result_p_l')
        status = str(row.get('status', '')).upper()
        if row.get('is_open') or ('OPEN' in status and 'FLIPPED OUT' not in status):
            countdown = countdown_span(row.get('effective_resolution_time'))
        elif row.get('is_closed') or any(label in status for label in
                                         ('WIN', 'LOSS', 'PUSH', 'VOID', 'SETTLED',
                                          'FLIPPED OUT')):
            countdown = '<span class="tui-muted">CLOSED</span>'
        else:
            countdown = '—'
        body.append([
            _clock(row.get('opened_time', row.get('time'))),
            _text(row.get('trader')),
            _text(row.get('original_market')),
            (_text(row.get('whale_side')), 'tui-accent'),
            countdown,
            (_text(row.get('status')), tone(row.get('status'))),
            (_price(row.get('slippage')), tone(row.get('slippage'), slippage=True)),
            (format_performance_value(
                pnl, display_mode, fixed_stake_dollars, signed=True)
             if isinstance(pnl, (int, float, Decimal)) else '—', tone(pnl, pnl=True)),
        ])
    return _table_html(title,
                       ['Opened', 'Trader', 'Market', 'Side', 'T−Resolve', 'Status', 'Slip', 'P/L'],
                       body, ['68px', '12%', '25%', '8%', '136px', '12%', '58px', '70px'],
                       note=note)


def open_bets_html(rows, limit=100, display_mode='$', fixed_stake_dollars=25):
    """Compact active-position table with its countdown beside side/status data."""
    body = []
    for row in rows[:limit]:
        body.append([
            _clock(row.get('time_opened')),
            _text(row.get('trader')),
            _text(row.get('original_market')),
            (_text(f"{row.get('side', '—')} / {row.get('direction', '—')}"), 'tui-accent'),
            countdown_span(row.get('expected_resolution_time')),
            (_text('OPEN'), 'tui-accent'),
            _price(row.get('our_entry')),
            (_price(row.get('slippage')), tone(row.get('slippage'), slippage=True)),
            format_performance_value(row.get('stake'), display_mode, fixed_stake_dollars),
            _text(f"{_decimal(row.get('contracts')):.2f}"
                  if _decimal(row.get('contracts')) is not None else '—'),
        ])
    return _table_html(
        'Open paper positions',
        ['Opened', 'Trader', 'Market', 'Side / Dir', 'T−Resolve', 'Status',
         'Entry', 'Slip', 'Stake', 'Contracts'],
        body, ['68px', '11%', '24%', '11%', '136px', '58px', '58px', '58px', '64px', '72px'])


def whales_html(rows, limit=None, display_mode='$', fixed_stake_dollars=25):
    body = []
    visible = rows if limit is None else rows[:limit]
    for row in visible:
        pnl = row.get('Net P/L')
        roi = row.get('ROI')
        body.append([
            _text(row.get('Trader')),
            _text(row.get('Trades')),
            _text(f"{row.get('Wins', 0)}-{row.get('Losses', 0)}-"
                  f"{row.get('Pushes', 0)}-{row.get('Voids', 0)}"),
            (format_performance_value(
                pnl, display_mode, fixed_stake_dollars, signed=True), tone(pnl, pnl=True)),
            (_percent(roi), tone(roi, pnl=True)),
        ])
    return _table_html('Whale performance', ['Trader', 'Settled', 'W-L-P-V', 'Net P/L', 'ROI'],
                       body, ['34%', '15%', '13%', '20%', '18%'])


def compact_grid_html(activity_rows, whale_stats, display_mode='$', fixed_stake_dollars=25):
    activity = activity_html(activity_rows, display_mode=display_mode,
                             fixed_stake_dollars=fixed_stake_dollars)
    whales = whales_html(whale_stats, display_mode=display_mode,
                         fixed_stake_dollars=fixed_stake_dollars)
    return f'<div class="tui-grid">{activity}{whales}</div>'
