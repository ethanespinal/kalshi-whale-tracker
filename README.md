# Kalshi Whale Tracker

A paper trading system that tracks prediction market whales, matches their bets to Kalshi markets, simulates entries, and measures which traders and strategies actually perform well over time.

The goal is simple: figure out whether whale activity can be turned into a useful trading signal.

## Status

Work in progress.

This project is currently paper trading only.

It does not place real money orders.

## What It Does

The bot listens for whale alerts, parses the bet, finds the matching Kalshi market, checks the current price, and decides whether the trade is worth copying.

It also tracks results over time so I can see which traders, sports, market types, and entry conditions perform best.

## Main Features

- Telegram whale alert tracking
- Kalshi market matching
- Game winner matching
- Spread matching
- Total matching
- Sports and esports support
- Paper trading
- Fixed paper bankroll
- Fixed position sizing
- Slippage checks
- Liquidity checks
- Whale performance filters
- Open exposure limits
- Duplicate protection
- Same-event conflict handling
- Trade recovery for previously unmatched alerts
- Settlement tracking
- SQLite database
- Persistent trades across restarts
- Streamlit dashboard
- P/L tracking
- Whale performance tracking
- Strategy analytics
- CSV reporting

## How It Works

Basic flow:

```text
Whale Alert
    ↓
Parse Market
    ↓
Identify Event
    ↓
Find Kalshi Market
    ↓
Match Contract
    ↓
Check Price
    ↓
Check Slippage
    ↓
Check Liquidity
    ↓
Check Risk Rules
    ↓
Paper Trade
    ↓
Track Settlement
    ↓
Analyze Performance
```

## Current Paper Setup

The project is still in testing.

Current paper settings:

```text
Starting bankroll: $10,000
Position size: $25
Maximum open exposure: $2,000
```

The bot only copies a trade when the Kalshi price is equal to or better than the whale's entry price.

```text
our price <= whale price
```

If the Kalshi price is worse, the signal can still be saved for research without opening a paper position.

## Whale Tracking

Each whale is tracked separately.

The system stores information like:

- trader name
- market
- side
- whale entry price
- simulated entry price
- slippage
- result
- profit or loss
- win rate
- ROI
- settled trade count

The goal is to learn which whales are actually worth following instead of blindly copying everyone.

## Conflicting Bets

The bot should not hold both sides of the same event at the same time.

If one whale takes one side and another whale later takes the opposite side, the system can compare their historical strength.

The stronger whale can be prioritized while both signals are still saved for research.

This avoids wasting exposure by betting against myself.

## Matching

One of the hardest parts of the project is matching whale alerts to the correct Kalshi contract.

The matcher handles differences in:

- team names
- city names
- abbreviations
- league names
- esports names
- spread lines
- total lines
- YES and NO contracts
- different market naming formats

Matching is intentionally conservative.

If the bot cannot confidently identify the correct market, it skips the trade instead of guessing.

## Recovery

Older alerts that failed because of matcher problems can be reprocessed later.

This is useful when the matching system improves.

Example:

```text
Old alert
    ↓
Previously unmatched
    ↓
Matcher gets improved
    ↓
Recovery process runs
    ↓
Market is found
    ↓
Check if event is still tradable
    ↓
Check current price
    ↓
Paper trade if still valid
```

This keeps old data from being completely wasted.

## Settlement

Open paper positions are stored in SQLite.

The bot continues checking them until the market settles.

Settled trades track:

- result
- payout
- gross P/L
- estimated fees
- net P/L
- ROI

Possible results include:

```text
WIN
LOSS
PUSH
VOID
```

## Dashboard

The Streamlit dashboard is designed to look more like a terminal than a normal web dashboard.

It includes:

- bankroll
- current P/L
- daily P/L
- open exposure
- open positions
- settled trades
- win rate
- whale performance
- recent activity
- all activity
- skipped alerts
- open bets
- settlement countdowns
- strategy analytics

## Analytics

The analytics section is used to figure out what is actually working.

Metrics include:

- equity curve
- daily P/L
- drawdown
- profit factor
- expectancy per trade
- average win
- average loss
- largest win
- largest loss
- max drawdown
- longest losing streak

Performance can also be grouped by:

- whale
- market type
- sport
- series
- slippage
- entry price
- time to resolution
- whale win rate
- whale ROI
- normal trades
- recovered trades

The goal is to improve the strategy using actual results instead of guessing.

## Project Structure

The exact structure is still changing, but the main files include:

```text
telegram_listener.py
alert_parser.py
matching.py
kalshi.py
simulator.py
database.py
series_map.py
names.py
dashboard.py
dashboard_data.py
record.py
config.py
requirements.txt
```

There is also a test folder for parser, matcher, trading, settlement, and analytics tests.

## Setup

Clone the repo:

```bash
git clone <your-repo-url>
cd kalshi-whale-tracker
```

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows:

```bash
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Variables

Create a `.env` file.

Store private credentials there.

Example:

```env
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_PHONE=
```

Do not commit `.env`.

## Run the Backend

```bash
python telegram_listener.py
```

This starts the whale listener and paper trading system.

## Run the Dashboard

Open another terminal:

```bash
streamlit run dashboard.py
```

## Generate Reports

```bash
python record.py
```

This can export paper trading results and analytics.

## Database

The project uses SQLite for persistent storage.

The database keeps:

- whale alerts
- parsed markets
- matched contracts
- skipped alerts
- paper trades
- open positions
- settled positions
- analytics data
- recovery history

Data should remain available after restarting the program.

## Safety

This project is currently built for testing and research.

It does not place real money trades.

Any future live trading support should only be added after the paper system has enough data to show that the strategy is stable.

## Current Limitations

The project is still under development.

Known areas that still need work:

- improving matching coverage
- improving esports matching
- improving cross-venue matching
- better whale scoring
- larger sample sizes
- better strategy diagnostics
- more testing
- better handling of conflicting signals
- better liquidity modeling
- more accurate fee modeling

## Planned Features

Possible future improvements:

- Rivo integration
- PredictBuddy and Rivo signal merging
- trader deduplication
- Polymarket whale tracking
- cross-venue signal matching
- Kalshi-only execution
- stronger whale scoring
- automatic conflict resolution
- improved analytics
- Raspberry Pi deployment
- dedicated touchscreen dashboard
- live hardware status display

## Long Term Goal

The long term goal is to build a system that can answer questions like:

```text
Which whales are actually profitable?

Which sports perform best?

Which market types perform best?

How much slippage kills the edge?

Does copying late still work?

Are recovered trades worse than fresh signals?

Which whales perform best in specific sports?

What happens when whales disagree?

What happens when multiple strong whales agree?
```

Once enough data is collected, the strategy can be improved using real performance instead of assumptions.

## Disclaimer

This project is for research, education, and paper trading.

Prediction markets involve financial risk.

Past performance does not guarantee future results.