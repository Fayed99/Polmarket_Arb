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

    def snapshot(self, msg):
        stamp = int(msg['timestamp'])
        if stamp <= 0 or stamp > time.time() * 1000 + 5000:
            raise ValueError('Invalid event timestamp')
        self.tick = str(msg.get('tick_size', self.tick))
        self.size_precision = precision(self.tick)
        self.minimum = float(msg.get('min_order_size', self.minimum))
        self.bids = dict(self.level(x['price'], x['size']) for x in msg['bids'] if float(x['size']) > 0)
        self.asks = dict(self.level(x['price'], x['size']) for x in msg['asks'] if float(x['size']) > 0)
        self.validate()
        self.event_ms, self.arrival_ms = stamp, time.time() * 1000
        self.hash, self.ready = msg.get('hash', ''), True

    def change(self, item, stamp):
        if not self.ready or stamp < self.event_ms:
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
                'tick': self.tick, 'minimum': self.minimum, 'size_precision': self.size_precision}


def integrity(market, settings):
    if not market or market.get('error'):
        return 'UNAVAILABLE', (market or {}).get('error', 'Discovering active market')
    if time.time() >= market['end'] or time.time() < market['start']:
        return 'ROLLOVER', 'Waiting for the next active market'
    if not market.get('connected'):
        return 'CONNECTING', 'Waiting for a complete WebSocket snapshot'
    books = market['books']
    if not all(b['ready'] and b['asks'] for b in books.values()):
        return 'STALE', 'Incomplete order books; execution is locked'
    now = time.time() * 1000
    if any(now - b['arrival_ms'] > settings.stale_ms or now - b['event_ms'] > settings.stale_ms or b['event_ms'] > now + 1000 for b in books.values()):
        return 'STALE', 'Order-book freshness limit exceeded'
    delta = abs(books['up']['event_ms'] - books['down']['event_ms'])
    if delta > settings.sync_ms:
        return 'UNSYNCED', f'UP / DOWN event times differ by {delta:.0f} ms (limit {settings.sync_ms} ms)'
    return 'LIVE', 'Synchronized live order books'