import { useEffect, useState } from 'react';
import { Clock3, ChevronDown } from 'lucide-react';
import { num } from '../lib/api';

const symbols = { BTC: '₿', ETH: 'Ξ', SOL: '◎' };
export const Countdown = ({ end }) => {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now() / 1000), 1000); return () => clearInterval(timer); }, []);
  const left = Math.max(0, Math.ceil((end || now) - now));
  return <span className={left < 30 ? 'amber' : ''} data-testid="market-countdown">{end ? `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}` : '--:--'}</span>;
};
export const MarketStrip = ({ markets = [], asset, select, disabled }) => <div className="market-strip">{['BTC', 'ETH', 'SOL'].map(symbol => {
  const m = markets.find(x => x.asset === symbol);
  return <button type="button" key={symbol} className={`market-tile ${asset === symbol ? 'selected' : ''}`} onClick={() => select(symbol)} disabled={disabled} data-testid={`market-tab-${symbol.toLowerCase()}`}>
    <div className="market-tile-top"><span className={`coin coin-${symbol.toLowerCase()}`}>{m?.image ? <img src={m.image} alt={symbol}/> : symbols[symbol]}</span><span className="market-name">{symbol}<span>{m?.name || {BTC:'Bitcoin',ETH:'Ethereum',SOL:'Solana'}[symbol]}</span></span><span className="duration-tag">5M</span><span className={`market-state ${m?.status === 'LIVE' ? 'up' : 'muted'}`}><i/>{m?.status === 'LIVE' ? 'LIVE' : m?.status || 'CONNECTING'}</span></div>
    <div className="market-prices" data-testid={`market-${symbol.toLowerCase()}-prices`}><span><span className="up">↗</span> UP <b>{m?.up_ask != null ? `${num(m.up_ask * 100, 1)}¢` : '—'}</b></span><span className="market-price-divider"/><span><span className="down">↘</span> DOWN <b>{m?.down_ask != null ? `${num(m.down_ask * 100, 1)}¢` : '—'}</b></span><span className="selected-check">{asset === symbol ? 'Selected' : 'View market'} <ChevronDown size={12}/></span></div>
  </button>;
})}<div className="market-universe"><span className="scan-bars"><i/><i/><i/><i/><i/></span><strong>3 markets</strong><span>One opportunity at a time</span><span className="small-caps"><Clock3 size={11}/> AUTO ROLLOVER</span></div></div>;