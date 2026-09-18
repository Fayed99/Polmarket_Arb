"""Binance public spot reference. Never used to settle or price Polymarket contracts."""
import asyncio
import copy
import json
import logging
import os
import time
import httpx
import websockets

log = logging.getLogger(__name__)


class SpotFeed:
    def __init__(self, registry, notify):
        self.assets = [r['asset'] for r in registry]
        self.rest, self.ws_url = os.environ['BINANCE_REST_URL'], os.environ['BINANCE_WS_URL']
        self.http = httpx.AsyncClient(timeout=10)
        self.notify = notify
        self.data, self.tasks = {}, []
        self.connected, self.stopping = False, False
        self.reconnects, self.last_notify = 0, 0

    def ensure_window(self, asset, start):
        if self.data.get(asset, {}).get('window_start') != start:
            self.data[asset] = {'asset': asset, 'symbol': f'{asset}USDT', 'window_start': start,
                                'window_end': start + 300, 'window_open': None, 'price': None,
                                'source_ms': 0, 'event_ms': 0, 'arrival_ms': 0,
                                'history': {}, 'error': None, 'bootstrap_at': 0}
        return self.data[asset]

    async def start(self):
        self.tasks = [asyncio.create_task(self.bootstrap_loop()), asyncio.create_task(self.stream_loop())]

    async def stop(self):
        self.stopping = True
        for t in self.tasks:
            t.cancel()
        if self.tasks:
            await asyncio.wait(self.tasks, timeout=3)
        await self.http.aclose()

    async def klines(self, asset, interval, start, limit):
        response = await self.http.get(f'{self.rest}/api/v3/klines', params={
            'symbol': f'{asset}USDT', 'interval': interval, 'startTime': start * 1000,
            'endTime': min(int(time.time() * 1000), (start + 300) * 1000 - 1), 'limit': limit})
        response.raise_for_status()
        return response.json()

    async def bootstrap(self, asset, start):
        data = self.ensure_window(asset, start)
        try:
            opening, history = await asyncio.gather(self.klines(asset, '5m', start, 1), self.klines(asset, '1s', start, 300))
            if not opening or int(opening[0][0]) != start * 1000:
                raise ValueError('Exact Binance opening candle unavailable')
            if self.data[asset]['window_start'] != start:
                return
            data['window_open'] = float(opening[0][1])
            now = time.time() * 1000
            for row in history:
                if start * 1000 <= int(row[0]) <= now:
                    # Do not overwrite a newer streamed price with older REST history.
                    data['history'].setdefault(int(row[0]) // 1000, float(row[4]))
            if data['price'] is None and history:
                last = history[-1]
                data['price'] = float(last[4])
                data['source_ms'] = min(int(last[6]), int(now))
                data['arrival_ms'] = now
            data['bootstrap_at'], data['error'] = time.time(), None
            await self.notify()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            data['error'] = f'Binance history unavailable: {type(exc).__name__}'
            log.warning('%s Binance bootstrap: %s', asset, exc)

    async def bootstrap_loop(self):
        while not self.stopping:
            start = int(time.time()) // 300 * 300
            needed = [a for a in self.assets if self.ensure_window(a, start)['window_open'] is None]
            if needed:
                await asyncio.gather(*(self.bootstrap(a, start) for a in needed))
                await asyncio.sleep(3)
            else:
                await asyncio.sleep(1)

    def apply_trade(self, msg):
        if msg.get('e') != 'aggTrade':
            return False
        asset = msg['s'].removesuffix('USDT')
        if asset not in self.assets:
            return False
        stamp = int(msg['T'])
        start = int(time.time()) // 300 * 300
        if not start * 1000 <= stamp < (start + 300) * 1000 or stamp > time.time() * 1000 + 5000:
            return False
        data = self.ensure_window(asset, start)
        if stamp < data['source_ms']:
            return False
        price = float(msg['p'])
        if not 0 < price < 1000000000:
            return False
        data.update(price=price, source_ms=stamp, event_ms=int(msg['E']), arrival_ms=time.time()*1000)
        data['history'][stamp // 1000] = price
        return True

    async def stream_loop(self):
        streams = '/'.join(f'{a.lower()}usdt@aggTrade' for a in self.assets)
        delay = 1
        while not self.stopping:
            try:
                async with websockets.connect(f'{self.ws_url}?streams={streams}', ping_interval=20, ping_timeout=20, close_timeout=2) as ws:
                    self.connected, delay = True, 1
                    while not self.stopping:
                        raw = await asyncio.wait_for(ws.recv(), 20)
                        envelope = json.loads(raw)
                        if self.apply_trade(envelope.get('data', envelope)) and time.monotonic() - self.last_notify >= 0.2:
                            self.last_notify = time.monotonic()
                            await self.notify()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning('Binance stream reconnect: %s', exc)
                self.connected = False
                self.reconnects += 1
                await self.notify()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 15)

    def snapshot(self, asset):
        data = self.data.get(asset)
        if not data:
            return {'asset': asset, 'status': 'CONNECTING', 'points': [], 'source': 'Binance', 'official_price_to_beat': None}
        now = time.time() * 1000
        age = max(0, now - data['source_ms']) if data['source_ms'] else None
        current_window = data['window_start'] == int(time.time()) // 300 * 300
        live = self.connected and data['event_ms'] > 0 and age is not None and age <= 10000 and current_window
        out = {k: copy.deepcopy(v) for k, v in data.items() if k != 'history'}
        out.update(status='LIVE' if live else 'STALE' if data['price'] is not None else 'CONNECTING',
                   points=[{'time': t, 'price': p} for t,p in sorted(data['history'].items())],
                   source='Binance', age_ms=round(age) if age is not None else None,
                   official_price_to_beat=None, reconnects=self.reconnects,
                   difference=data['price']-data['window_open'] if data['price'] is not None and data['window_open'] is not None else None)
        return out