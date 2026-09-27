"""Paper entries at fresh displayed asks; no order placement exists here."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import logging
import time

from alert_parser import parse_alert
from config import Settings
from kalshi import LookupUnavailable
from matching import MATCHED, Matcher, contract_side, number
from names import normalize

log = logging.getLogger(__name__)


def units(value):
    value = number(value)
    if value is None or value * 10000 != (value * 10000).to_integral_value():
        return None
    return int(value * 10000)


def estimated_fee(quantity, price):
    # Configured V1 estimate, not an exchange fee quote: 7% * n * p * (1-p),
    # rounded up to a cent. Series-specific fees/rebates are not modeled.
    p = Decimal(price) / 10000
    cents = (Decimal('0.07') * quantity * p * (1 - p) * 100).to_integral_value(rounding=ROUND_CEILING)
    return int(cents) * 100


def _bounded(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


def whale_strength_score(win_rate, roi, profile):
    """Conservative 0-100 quality score with sample-size shrinkage.

    Alert metrics remain useful, but tracked outcomes pull every empirical
    component toward a 50% prior until the whale has a meaningful sample.
    """
    overall = profile.get('overall') or {}
    sample = int(overall.get('decided') or 0)
    confidence = sample / (sample + 15)
    displayed_roi_quality = _bounded(50 + float(roi or 0) / 4)
    displayed_quality = .75 * _bounded(win_rate or 0) + .25 * displayed_roi_quality
    displayed_adjusted = 50 + (displayed_quality - 50) * (.4 + .6 * confidence)

    def empirical(bucket):
        bucket = bucket or {}
        decided = int(bucket.get('decided') or 0)
        wins = int(bucket.get('wins') or 0)
        posterior_win = (wins + 10) / (decided + 20) * 100
        raw_roi = float(bucket.get('roi') or 0)
        shrunk_roi = raw_roi * decided / (decided + 20)
        return .8 * posterior_win + .2 * _bounded(50 + shrunk_roi / 4)

    components = {
        'displayed_adjusted': displayed_adjusted,
        'overall': empirical(overall),
        'recent': empirical(profile.get('recent')),
        'context': empirical(profile.get('context')),
        'sample_size': sample,
        'confidence': confidence,
    }
    score = (.5 * components['displayed_adjusted'] + .3 * components['overall'] +
             .1 * components['recent'] + .1 * components['context'])
    return round(score, 3), components


def quote_exit(market, side, quantity):
    """Price a paper sale of an existing YES/NO position at displayed bid."""
    if market.get('status') not in ('active', 'open') or market.get('result'):
        raise ValueError('market is not open for exit')
    price = units(market.get(f'{side}_bid_dollars'))
    size = number(market.get(f'{side}_bid_size_fp'))
    if side == 'no' and size is None:
        ask = units(market.get('yes_ask_dollars'))
        if ask is not None and price == 10000 - ask:
            size = number(market.get('yes_ask_size_fp'))
    if price is None or not 0 < price < 10000:
        raise ValueError('missing or invalid executable exit bid')
    if size is None or size < Decimal(str(quantity)):
        raise ValueError('insufficient displayed exit liquidity')
    gross = int((Decimal(str(quantity)) * price).to_integral_value(rounding=ROUND_HALF_UP))
    fee = estimated_fee(Decimal(str(quantity)), price)
    return {'exit_price': price, 'exit_fee': fee,
            'exit_gross_proceeds': gross, 'exit_proceeds': gross - fee}


def _timestamp(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        close = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    if close.tzinfo is None:
        return None
    return close.astimezone(timezone.utc)


def get_effective_event_time(market, event=None):
    """Choose the best exchange-supplied sports occurrence/resolution time.

    Kalshi sports ``close_time`` may be an administrative last-settlement bound,
    so scheduled occurrence and expected expiration take precedence. Event data
    is considered for each field before moving to a weaker field family.
    """
    event = event or {}
    for field in ('occurrence_datetime', 'expected_expiration_time',
                  'close_time', 'latest_expiration_time'):
        for owner, payload in (('market', market), ('event', event)):
            value = _timestamp(payload.get(field))
            if value is not None:
                return value, f'{owner}.{field}'
    raise ValueError('Reliable market resolution time unavailable')


def get_hours_until_resolution(market, alert_time, event=None):
    """Return a centralized timing diagnostic for the duration strategy gate."""
    resolution_time, source = get_effective_event_time(market, event)
    alert_datetime = datetime.fromtimestamp(alert_time, timezone.utc)
    raw_hours = (resolution_time - alert_datetime).total_seconds() / 3600
    can_close_early = market.get('can_close_early')
    if can_close_early is None:
        can_close_early = (event or {}).get('can_close_early')
    return {
        # A scheduled time in the past represents an in-progress event, not a
        # negative holding period. Zero keeps live markets eligible and bucketed.
        'hours': max(0.0, raw_hours),
        'raw_hours': raw_hours,
        'source': source,
        'timestamp': resolution_time.isoformat().replace('+00:00', 'Z'),
        'can_close_early': can_close_early,
    }


def get_trading_close_bound(market, event=None):
    """Return a future-bound field used only by the existing open-market guard."""
    event = event or {}
    for field in ('close_time', 'latest_expiration_time'):
        for payload in (market, event):
            value = _timestamp(payload.get(field))
            if value is not None:
                return value.isoformat().replace('+00:00', 'Z')
    return None


def hours_to_close(market, event, alert_time):
    """Compatibility wrapper for callers using the original helper name."""
    return get_hours_until_resolution(market, alert_time, event)['hours']


def whale_quality_reason(alert, settings):
    win_rate = number(alert.get('win_rate'))
    roi = number(alert.get('roi'))
    win_failed = win_rate is None or win_rate < Decimal(str(settings.min_whale_win_rate))
    roi_failed = roi is None or roi < Decimal(str(settings.min_whale_roi))
    if win_failed and roi_failed:
        return 'Whale quality below strategy thresholds'
    if win_failed:
        return 'Whale win rate below strategy minimum'
    if roi_failed:
        return 'Whale ROI below strategy minimum'
    return ''


def positive_slippage_reason(executable_price, whale_price, settings):
    """Classify adverse executable slippage after quote/liquidity validation."""
    whale = units(whale_price)
    maximum = units(settings.max_allowed_slippage)
    if whale is None or maximum is None:
        raise ValueError('Invalid slippage strategy configuration')
    if executable_price - whale > maximum:
        return 'Positive slippage above strategy maximum'
    return ''


def strategy_mode_reason(alert, settings):
    """Return a research-only reason for valid markets excluded by the preset."""
    if settings.strategy_mode == 'all_supported':
        return ''
    if alert.get('market_type') != 'game_winner':
        if settings.strategy_mode == 'conservative':
            return 'Filtered by conservative strategy mode'
        return 'Filtered by moneyline-only strategy mode'
    return ''


def quote_order(market, side, whale_price, settings, now=None):
    now = now or datetime.now(timezone.utc)
    if market.get('status') not in ('active', 'open') or market.get('result'):
        raise ValueError('Market is no longer open')
    if units(market.get('notional_value_dollars')) != 10000:
        raise ValueError('Only $1 binary contracts are supported')
    try:
        close = _timestamp(market['close_time'])
        if close is None:
            raise ValueError('Missing/invalid close time')
        if close <= now:
            raise ValueError('Market close time has passed')
    except (KeyError, TypeError) as exc:
        raise ValueError('Missing/invalid close time') from exc
    price = units(market.get(f'{side}_ask_dollars'))
    whale = units(whale_price)
    if price is None or not 0 < price < 10000 or whale is None:
        raise ValueError('Missing or invalid ask/alert price')
    size = number(market.get(f'{side}_ask_size_fp'))
    if side == 'no' and size is None:
        # NO asks are resting YES bids, but require consistent quoted prices.
        bid = units(market.get('yes_bid_dollars'))
        if bid is not None and price == 10000 - bid:
            size = number(market.get('yes_bid_size_fp'))
    if size is None or size < 1:
        raise ValueError('No verifiable displayed liquidity')
    quantity = Decimal(settings.fixed_stake) / price
    if size < quantity:
        raise ValueError('Insufficient displayed liquidity for full $25 paper stake')
    return quantity, price, estimated_fee(quantity, price)


class PaperTrader:
    def __init__(self, client, database, settings=None, on_result=None,
                 on_exposure_change=None, session_id='legacy',
                 strategy_version=None, code_version='unknown',
                 backend_run_id='legacy'):
        self.client = client
        self.db = database
        self.settings = settings or Settings()
        self.matcher = Matcher(client)
        self.on_result = on_result
        self.on_exposure_change = on_exposure_change
        self.exposure_closed = None
        self.session_id = session_id
        self.strategy_version = strategy_version or self.settings.strategy_version
        self.code_version = code_version
        self.backend_run_id = backend_run_id

    def start_backend(self, chat_id=None):
        """Recover the durable queue/ledger and refresh settlements before work."""
        recovered_alerts = self.db.recover_processing()
        counts = self.db.recovery_counts()
        self.db.start_backend(
            self.session_id, self.strategy_version, self.code_version,
            self.backend_run_id, recovered_alerts, counts['open_trades'],
            strategy_mode=self.settings.strategy_mode)
        settled = self.settle()
        offset = self.db.telegram_offset(chat_id) if chat_id is not None else None
        return {
            'recovered_alerts': recovered_alerts,
            'open_trades': counts['open_trades'],
            'settled': settled,
            'last_processed_message_id': (
                offset['last_processed_message_id'] if offset else 0),
        }

    def stop_backend(self):
        self.db.stop_backend(self.backend_run_id)

    def heartbeat(self):
        exposure = self.db.portfolio_stats()['open_exposure']
        self.db.update_backend_status(last_heartbeat=time.time(),
                                      current_open_exposure=exposure)

    def _sync_exposure_state(self):
        closed = not self.db.exposure_allows(
            self.settings.fixed_stake, self.settings.max_exposure)
        previous = self.exposure_closed
        self.exposure_closed = closed
        message = None
        if closed and previous is not True:
            message = '⏸ EXPOSURE CLOSED — waiting for positions to settle'
        elif not closed and previous is True:
            message = '▶ EXPOSURE REOPENED — paper entries resumed'
        if message and self.on_exposure_change is not None:
            try:
                self.on_exposure_change(message)
            except Exception:
                log.exception('Could not display exposure state change')
        return closed

    def process_next(self):
        if self._sync_exposure_state():
            row = self.db.claim(self.backend_run_id)
            if row is None:
                return False
            self._reject_for_exposure(row)
            return True
        row = self.db.claim(self.backend_run_id)
        if row is None:
            return False
        return self._process_claimed(row)

    def process_alert_id(self, alert_id):
        """Process one explicitly prepared recovery row through normal controls."""
        row = self.db.claim_alert(alert_id, self.backend_run_id)
        if row is None:
            return False
        if self._sync_exposure_state():
            self._reject_for_exposure(row)
            self.db.finalize_recovery(row['id'])
            return True
        return self._process_claimed(row)

    def _reject_for_exposure(self, row):
        reason = 'Exposure closed; waiting for positions to settle'
        self.db.finish(row['id'], 'skipped', reason,
                       {'shadow': {'reason': reason}})
        log.info('Alert %s preserved without entry: %s', row['source_key'], reason)

    def _process_claimed(self, row):
        details = {}
        try:
            age = time.time() - row['received_at']
            if age > self.settings.max_alert_age or age < -60:
                self.db.finish(row['id'], 'skipped', 'Stale or future-dated alert')
                return True
            alert = parse_alert(row['raw_text'])
            if alert is None:
                self.db.finish(row['id'], 'invalid', 'Alert parser rejected message')
                return True
            details['alert'] = alert
            self.db.checkpoint(row['id'], details, parsed=alert)
            self.db.update_backend_status(last_kalshi_lookup=time.time())
            match = self.matcher.match(alert)
            details['match'] = asdict(match)
            self.db.checkpoint(row['id'], details, parsed=alert, match=details['match'])
            if match.status != MATCHED:
                self.db.finish(row['id'], match.status, match.reason, details)
                return True
            market = self.client.market(match.market['ticker'])
            self.db.update_backend_status(last_kalshi_lookup=time.time())
            details['quote'] = market
            details['quote_fetched_at'] = time.time()
            if (market.get('event_ticker') != match.event['event_ticker'] or
                    contract_side(alert, market, match.event,
                                  require_open=False) != match.side):
                raise ValueError('Fresh contract no longer matches alert')
            if time.time() - row['received_at'] > self.settings.max_alert_age:
                raise ValueError('Alert became stale during lookup')
            timing = get_hours_until_resolution(market, row['received_at'], match.event)
            close_hours = timing['hours']
            details['hours_to_close'] = close_hours
            details['resolution_timing'] = timing
            log.info('Alert %s resolution time: %s=%s (%.2fh, raw %.2fh, can_close_early=%s)',
                     row['source_key'], timing['source'], timing['timestamp'],
                     timing['hours'], timing['raw_hours'], timing['can_close_early'])
            details['strategy_filters'] = {
                'max_hours_to_close': self.settings.max_hours_to_close,
                'min_whale_win_rate': self.settings.min_whale_win_rate,
                'min_whale_roi': self.settings.min_whale_roi,
                'max_allowed_slippage': self.settings.max_allowed_slippage,
                'strategy_mode': self.settings.strategy_mode,
            }
            if close_hours > self.settings.max_hours_to_close:
                raise ValueError('Market closes too far in the future')
            quote_market = market
            if _timestamp(market.get('close_time')) is None:
                # This bound only proves that an active quote has not reached
                # its administrative end; it does not drive duration eligibility.
                quote_market = {**market,
                                'close_time': get_trading_close_bound(market, match.event)}
            quantity, price, fee = quote_order(quote_market, match.side, alert['price'], self.settings)
            whale_price = units(alert['price'])
            slippage_units = price - whale_price
            details['slippage'] = {
                'whale_price_units': whale_price,
                'executable_price_units': price,
                'slippage_units': slippage_units,
                'slippage_dollars': f'{Decimal(slippage_units) / 10000:.4f}',
            }
            slippage_reason = positive_slippage_reason(
                price, alert['price'], self.settings)
            quality_reason = whale_quality_reason(alert, self.settings)
            mode_reason = strategy_mode_reason(alert, self.settings)
            contract_key = normalize(
                market.get('yes_sub_title') or market.get('title') or market['ticker'])
            shadow_reason = '; '.join(
                reason for reason in (mode_reason, slippage_reason, quality_reason) if reason)
            trade_mode = 'SHADOW_ONLY' if shadow_reason else 'PAPER'
            conflict_resolution = None
            series_ticker = match.event.get('series_ticker')
            if trade_mode == 'PAPER' and alert.get('market_type') == 'game_winner':
                conflict = self.db.open_winner_conflict(
                    match.event.get('event_ticker'), market['ticker'], contract_key, match.side)
                if conflict:
                    old_profile = self.db.whale_performance_profile(
                        conflict.get('trader'), 'game_winner', conflict.get('series_ticker'))
                    new_profile = self.db.whale_performance_profile(
                        alert.get('trader'), 'game_winner', series_ticker)
                    old_score, old_components = whale_strength_score(
                        conflict.get('whale_win_rate'), conflict.get('whale_roi'), old_profile)
                    new_score, new_components = whale_strength_score(
                        alert.get('win_rate'), alert.get('roi'), new_profile)
                    improvement = round(new_score - old_score, 3)
                    conflict_resolution = {
                        'action': 'keep', 'existing_trade_id': conflict['trade_id'],
                        'existing_whale_score': old_score, 'new_whale_score': new_score,
                        'score_improvement': improvement,
                        'required_improvement': self.settings.min_whale_score_improvement,
                        'existing_score_components': old_components,
                        'new_score_components': new_components,
                        'existing_profile': old_profile, 'new_profile': new_profile,
                        'reason': 'Opposite side already open; new whale not clearly stronger',
                    }
                    if improvement >= self.settings.min_whale_score_improvement:
                        try:
                            exit_market = self.client.market(conflict['ticker'])
                            self.db.update_backend_status(last_kalshi_lookup=time.time())
                            exit_quote = quote_exit(
                                exit_market, conflict['direction'], conflict['quantity'])
                            reversal_cost = (
                                max(0, conflict['stake'] - exit_quote['exit_gross_proceeds']) +
                                conflict['fee'] + exit_quote['exit_fee'] + fee)
                            conflict_resolution.update(exit_quote)
                            conflict_resolution['estimated_reversal_cost'] = reversal_cost
                            conflict_resolution['max_reversal_cost'] = units(
                                self.settings.max_reversal_cost)
                            if reversal_cost <= units(self.settings.max_reversal_cost):
                                conflict_resolution.update({
                                    'action': 'flip',
                                    'reason': 'Stronger opposite whale exceeded reversal threshold',
                                })
                            else:
                                conflict_resolution['reason'] = (
                                    'Reversal blocked: estimated fees/slippage exceed limit')
                        except ValueError as exc:
                            conflict_resolution['reason'] = (
                                f'Reversal blocked: current position cannot be exited ({exc})')
            status = self.db.enter(row['id'], market['ticker'], match.side, quantity, price,
                                   fee, self.settings.max_exposure, details,
                                   self.settings.max_open_positions, stake=self.settings.fixed_stake,
                                   session_id=self.session_id,
                                   strategy_version=self.strategy_version,
                                   code_version=self.code_version,
                                   trade_mode=trade_mode,
                                   shadow_reason=shadow_reason,
                                   whale_win_rate=float(alert['win_rate']),
                                   whale_roi=float(alert['roi']),
                                   hours_to_close=close_hours,
                                   resolution_time_source=timing['source'],
                                   effective_resolution_time=timing['timestamp'],
                                   can_close_early=timing['can_close_early'],
                                   backend_run_id=self.backend_run_id,
                                   strategy_mode=self.settings.strategy_mode,
                                   event_ticker=match.event.get('event_ticker'),
                                   market_type=alert.get('market_type'),
                                   winner_contract_key=contract_key,
                                   series_ticker=series_ticker,
                                   conflict_resolution=conflict_resolution)
            if status not in ('traded', 'shadow_only'):
                self.db.finish(row['id'], 'skipped', status, details)
            log.info('Alert %s: %s %s %s', row['source_key'], status, market['ticker'], match.side)
        except LookupUnavailable as exc:
            status = 'retryable' if row['attempts'] < 3 else 'error'
            self.db.finish(row['id'], status, str(exc), details, retry_delay=60)
            log.warning('Alert %s: %s', row['source_key'], exc)
        except ValueError as exc:
            self.db.finish(row['id'], 'skipped', str(exc), details)
        except Exception as exc:
            # Keep the listener alive and preserve diagnostics; never invent a fill.
            self.db.finish(row['id'], 'error', f'{type(exc).__name__}: {exc}', details)
            log.exception('Alert %s processing failed', row['source_key'])
        finally:
            self.db.finalize_recovery(row['id'])
            if self.on_result is not None:
                try:
                    self.on_result(*self.db.alert_result(row['id']))
                except Exception:
                    # Display failures must never change a recorded decision.
                    log.exception('Could not display alert %s', row['source_key'])
            self._sync_exposure_state()
        return True

    def settle(self):
        settled = 0
        try:
            for trade in self.db.unresolved_trades():
                try:
                    self.db.update_backend_status(last_kalshi_lookup=time.time())
                    market = self.client.market(trade['ticker'])
                    if market.get('status') not in ('settled', 'finalized'):
                        continue
                    result = str(market.get('result') or '').lower()
                    settlement_value = units(market.get('settlement_value_dollars'))
                    if result in ('yes', 'no'):
                        settled += self.db.settle(trade['id'], result)
                    elif settlement_value is not None:
                        settled += self.db.settle(trade['id'], result, settlement_value)
                    else:
                        log.warning('Settlement deferred for %s: resolved market has no usable result or value',
                                    trade['ticker'])
                except LookupUnavailable as exc:
                    log.warning('Settlement deferred: %s', exc)
                    break
        finally:
            exposure = self.db.portfolio_stats()['open_exposure']
            self.db.update_backend_status(last_settlement_refresh=time.time(),
                                          current_open_exposure=exposure)
            self._sync_exposure_state()
        return settled
