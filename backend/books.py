"""Public CLOB books. Event time and local receipt time are deliberately separate."""
import time
from decimal import Decimal
from py_clob_client_v2.order_builder.builder import ROUNDING_CONFIG


def precision(tick):
    key = str(Decimal(str(tick)).normalize())
    if key not in ROUNDING_CONFIG:
        raise ValueError('Unsupported exchange tick size')
    return ROUNDING_CONFIG[key].size


class Book:
    def __init__(self, token, tick, minimum):
        self.token, self.tick, self.minimum = token, str(tick), float(minimum)
        self.size_precision = precision(tick)
        self.bids, self.asks = {}, {}
        self.event_ms, self.arrival_ms = 0, 0
        self.ready, self.hash = False, ''
        self.initialized = False
        self.anomaly_since = None
        self.snapshot_ms = 0
        self.timestamp_regressions = 0

    def level(self, price, size):
        p, q = Decimal(str(price)), Decimal(str(size))
        if not p.is_finite() or not q.is_finite() or not 0 < p < 1 or q < 0:
            raise ValueError('Invalid book level')
        if p % Decimal(self.tick) != 0:
            raise ValueError('Off-tick book price')
        return float(p), float(q)

    def validate(self):
        if self.bids and self.asks and max(self.bids) >= min(self.asks):
            raise ValueError('Crossed order book')

    def snapshot(self, msg, defer_validation=False):
        stamp = int(msg['timestamp'])
        if stamp <= 0 or stamp > time.time() * 1000 + 5000:
            raise ValueError('Invalid event timestamp')
        self.tick = str(msg.get('tick_size', self.tick))
        self.size_precision = precision(self.tick)
        self.minimum = float(msg.get('min_order_size', self.minimum))
        bids = dict(self.level(x['price'], x['size']) for x in msg['bids'] if float(x['size']) > 0)
        asks = dict(self.level(x['price'], x['size']) for x in msg['asks'] if float(x['size']) > 0)
        self.bids, self.asks = bids, asks
        self.initialized = True
        if not defer_validation:
            self.validate()
        self.event_ms, self.arrival_ms = stamp, time.time() * 1000
        self.snapshot_ms = stamp
        self.hash, self.ready = msg.get('hash', ''), True

    def change(self, item, stamp):
        if not self.initialized or stamp < self.event_ms:
            raise ValueError('Missing snapshot or out-of-order event')
        if stamp > time.time() * 1000 + 5000:
            raise ValueError('Future event timestamp')
        if item['side'] not in ('BUY', 'SELL'):
            raise ValueError('Unknown order side')
        p, q = self.level(item['price'], item['size'])
        side = self.bids if item['side'] == 'BUY' else self.asks
        if q == 0:
            side.pop(p, None)
        else:
            side[p] = q
        self.event_ms, self.arrival_ms = stamp, time.time() * 1000
        self.hash = item.get('hash', '')

    def public(self):
        return {'token_id': self.token, 'bids': sorted(self.bids.items(), reverse=True),
                'asks': sorted(self.asks.items()), 'event_ms': self.event_ms,
                'arrival_ms': self.arrival_ms, 'ready': self.ready, 'hash': self.hash,
                'tick': self.tick, 'minimum': self.minimum, 'size_precision': self.size_precision,
                'timestamp_regressions': self.timestamp_regressions,
                'recovering': self.anomaly_since is not None}


def integrity(market, settings):
    if not market or market.get('error'):
        return 'UNAVAILABLE', (market or {}).get('error', 'Discovering active market')
    if time.time() >= market['end'] or time.time() < market['start']:
        return 'ROLLOVER', 'Waiting for the next active market'
    if not market.get('connected'):
        return 'CONNECTING', 'Waiting for a complete WebSocket snapshot'
    books = market['books']
    if set(books) != {'up', 'down'} or not all(b['ready'] for b in books.values()):
        return 'RECOVERING', 'Validating the order books. Trading resumes automatically when integrity is restored.'
    if not all(b['asks'] for b in books.values()):
        return 'NO_LIQUIDITY', 'One outcome has no asks available. Waiting for executable liquidity.'
    now = time.time() * 1000
    if any(now - b['arrival_ms'] > settings.stale_ms or now - b['event_ms'] > settings.stale_ms or b['event_ms'] > now + 1000 for b in books.values()):
        return 'STALE', 'Order-book freshness limit exceeded'
    delta = abs(books['up']['event_ms'] - books['down']['event_ms'])
    if delta > settings.sync_ms:
        return 'UNSYNCED', f'UP / DOWN event times differ by {delta:.0f} ms (limit {settings.sync_ms} ms)'
    return 'LIVE', 'Synchronized live order books'


def side_integrity(market, side, settings):
    """A single-outcome order does not combine books, so cross-book sync is irrelevant."""
    if not market or market.get('error'):
        return 'UNAVAILABLE', (market or {}).get('error') or 'Discovering active market'
    if not market.get('start', 0) <= time.time() < market.get('end', 0):
        return 'ROLLOVER', 'Waiting for the next active market'
    book = market.get('books', {}).get(side)
    if not market.get('connected') or not book or not book['ready']:
        return 'RECOVERING', 'Waiting for a verified order-book snapshot'
    if not book['asks']:
        return 'NO_LIQUIDITY', f'No {side.upper()} asks are currently available'
    now = time.time() * 1000
    if now - book['arrival_ms'] > settings.stale_ms or now - book['event_ms'] > settings.stale_ms or book['event_ms'] > now + 1000:
        return 'STALE', f'{side.upper()} order book is stale'
    return 'LIVE', 'Fresh single-outcome book'