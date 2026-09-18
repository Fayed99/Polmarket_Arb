import { useCallback, useEffect, useRef, useState } from 'react';
import { BrowserRouter, Routes, Route, useNavigate } from 'react-router-dom';
import { ArrowUpRight, BookOpen, CircleHelp, Pause, Play, Radio, RefreshCw, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';
import { Toaster } from './components/ui/sonner';
import { Button } from './components/ui/button';
import { Shell } from './components/Shell';
import { MarketStrip } from './components/MarketStrip';
import { StrategyPanel } from './components/StrategyPanel';
import { DecisionPanel } from './components/DecisionPanel';
import { SpotChart } from './components/SpotChart';
import { ManualTradePanel } from './components/ManualTradePanel';
import { SharePriceBar } from './components/SharePriceBar';
import { ActivityPage, FeedPage, SettingsPage, SimulatorPage } from './pages/WorkspacePages';
import { API, api, sessionId, errorText } from './lib/api';
import './App.css';
import './Trading.css';

const Optimizer = ({ data, connected, save, saving, execute, executing, onTrade, reconnect }) => {
  const navigate = useNavigate();
  return <><div className="page-heading"><div><div className="eyebrow"><span/> CAPITAL EFFICIENCY, AUTOMATED</div><h1>Auto optimizer<span className="heading-dot">.</span></h1><p>Live perspective. Independent trades. Smarter allocation.</p></div><div className="heading-actions"><a className="readme-link" href={`${API}/readme`} data-testid="download-readme-link" title="Download the complete user guide"><BookOpen size={14}/><span>User guide</span></a><span className={`scanning-label ${data?.settings?.paused ? 'muted' : ''}`}><span className="scan-dot"/>{data?.settings?.paused ? 'Auto paused' : 'Scanning live markets'}</span><Button variant="outline" size="sm" data-testid="pause-optimizer-button" disabled={saving || !data} onClick={() => save({...data.settings, paused: !data.settings.paused})}>{data?.settings?.paused ? <Play size={13}/> : <Pause size={13}/>} {data?.settings?.paused ? 'Resume' : 'Pause'}</Button></div></div>
    <MarketStrip markets={data?.markets} asset={data?.settings?.asset || 'BTC'} disabled={saving || !data} select={asset => save({...data.settings, asset})}/>
    {!connected && <div className="stream-recovery-banner" data-testid="stream-recovery-banner"><Radio size={14}/><span>Reconnecting to market updates. Last-known prices are shown; buying is paused.</span><button onClick={reconnect} data-testid="reconnect-stream-button"><RefreshCw size={13}/> Reconnect</button></div>}
    <SharePriceBar data={data} connected={connected}/>
    <div className="market-workbench"><SpotChart reference={data?.reference} asset={data?.settings?.asset || 'BTC'} connected={connected}/><ManualTradePanel data={data} connected={connected} onTrade={onTrade}/></div>
    <div className="optimizer-section-label"><span><ShieldCheck size={14}/> AUTO ALLOCATION</span><span>Two outcomes. One calculated decision.</span></div>
    <div className="workspace-grid"><StrategyPanel settings={data?.settings} save={save} saving={saving}/><DecisionPanel data={data} connected={connected} execute={execute} executing={executing}/></div>
    <div className="health-strip"><div><Radio size={15}/><span>FEED HEALTH</span><strong className={data?.diagnostics?.status === 'LIVE' ? 'up' : 'amber'} data-testid="feed-status">{data?.diagnostics?.status || 'CONNECTING'}</strong></div><div><span>Book synchronization</span><b data-testid="sync-lag">{data?.diagnostics?.sync_ms ?? '—'} ms</b></div><div><span>Recalculation</span><b>Event-driven + 1s fallback</b></div><div><ShieldCheck size={13}/><span>Fees included</span></div><button onClick={() => navigate('/feed')} data-testid="view-diagnostics-button">View diagnostics <ArrowUpRight size={13}/></button></div>
  </>;
};

function Workspace() {
  const [data, setData] = useState(null), [connected, setConnected] = useState(false);
  const [saving, setSaving] = useState(false), [executing, setExecuting] = useState(false);
  const [tradeVersion, setTradeVersion] = useState(0);
  const [connectionVersion, setConnectionVersion] = useState(0);
  const lastMessage = useRef(0);
  useEffect(() => {
    const source = new EventSource(`${API}/stream?session_id=${sessionId}`);
    let pending = null;
    const accept = next => { setData(prev => ({...next, settings: prev && JSON.stringify(prev.settings) === JSON.stringify(next.settings) ? prev.settings : next.settings})); setConnected(true); };
    source.onmessage = event => { try { lastMessage.current = Date.now(); pending = JSON.parse(event.data); } catch { setConnected(false); } };
    // Keep live rendering smooth without creating a React render for every upstream tick.
    const renderer = setInterval(() => { if(pending) { accept(pending); pending=null; } }, 120);
    source.onerror = () => { if (Date.now() - lastMessage.current > 4000) setConnected(false); };
    source.addEventListener('feed-error', () => setConnected(false));
    const watchdog = setInterval(() => { if (Date.now() - lastMessage.current > 4000) setConnected(false); }, 1000);
    return () => { source.close(); clearInterval(watchdog); clearInterval(renderer); };
  }, [connectionVersion]);
  const save = useCallback(async settings => {
    setSaving(true);
    try { const {data: saved} = await api.put('/settings', settings); setData(prev => prev ? {...prev, settings: saved, decision: {...prev.decision, status: 'CALCULATING', deployed: 0, reason: 'Recalculating with your new parameters'}} : prev); toast.success('Strategy parameters saved'); }
    catch (e) { toast.error(errorText(e)); }
    finally { setSaving(false); }
  }, []);
  const execute = useCallback(async (quote_id, scenario = 'normal') => {
    setExecuting(true);
    try { const {data: record} = await api.post('/paper/execute', {quote_id, scenario, request_id: crypto.randomUUID()}); setTradeVersion(x => x+1); if (record.status === 'LOCKED_POSITION') toast.success('Paper trade filled. Both legs reconciled.'); else if (record.status === 'UNWOUND') toast.warning('One-sided fill recovered. Paper exposure unwound.'); else toast.warning(`${record.status.replaceAll('_',' ')}: ${record.events.at(-1)?.message}`); return record; }
    catch (e) { toast.error(errorText(e)); }
    finally { setExecuting(false); }
  }, []);
  return <Shell data={data} connected={connected}><Routes>
    <Route path="/" element={<Optimizer {...{data, connected, save, saving, execute, executing}} onTrade={()=>setTradeVersion(v=>v+1)} reconnect={()=>setConnectionVersion(v=>v+1)}/>}/>
    <Route path="/positions" element={<ActivityPage positionsOnly version={tradeVersion}/>}/>
    <Route path="/activity" element={<ActivityPage version={tradeVersion}/>}/>
    <Route path="/feed" element={<FeedPage data={data}/>}/>
    <Route path="/settings" element={<SettingsPage {...{data, save, saving}}/>}/>
    <Route path="/simulator" element={<SimulatorPage {...{data, connected, execute, executing}}/>}/>
    <Route path="*" element={<div className="empty-state"><CircleHelp/><h1>Page not found</h1><a data-testid="not-found-home-link" href="/">Return to optimizer</a></div>}/>
  </Routes><Toaster theme="dark" position="bottom-right" richColors closeButton/></Shell>;
}
export default function App() { return <BrowserRouter><Workspace/></BrowserRouter>; }