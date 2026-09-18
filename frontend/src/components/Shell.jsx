import { NavLink } from 'react-router-dom';
import { ArrowUpRight, ArrowDownLeft, AudioLines, ChartNoAxesCombined, FlaskConical, History, Layers3, LockKeyhole, Radio, Settings2, ShieldCheck, Wallet, Zap } from 'lucide-react';
import { money } from '../lib/api';

export const Shell = ({ children, data, connected }) => <div className="app-shell">
  <aside className="sidebar">
    <NavLink to="/" className="brand" data-testid="brand-home"><span className="brand-symbol"><ArrowUpRight/><ArrowDownLeft/></span><span>parity<span className="brand-period">.</span></span></NavLink>
    <div className="workspace-label">TRADING WORKSPACE</div>
    <nav>{[
      ['/', Zap, 'Auto optimizer'], ['/positions', Layers3, 'Positions'], ['/activity', History, 'Trade activity'],
      ['/simulator', FlaskConical, 'Paper lab'], ['/feed', Radio, 'Market data'], ['/settings', Settings2, 'Settings']
    ].map(([to, Icon, label]) => <NavLink end={to === '/'} to={to} key={to} data-testid={`nav-${to === '/' ? 'optimizer' : to.slice(1)}`} className={({ isActive }) => `nav-item ${isActive ? 'active' : ''}`}><Icon size={18}/><span>{label}</span>{to === '/' && <i/>}</NavLink>)}</nav>
    <div className="sidebar-bottom"><div className="venue"><ChartNoAxesCombined size={18}/><span>Polymarket<span>CLOB · 5-minute markets</span></span><ArrowUpRight size={14}/></div><div className="safe-note"><ShieldCheck size={15}/><span>Your capital. Your constraints.</span></div><div className="version">PARITY TERMINAL <span>v1.0</span></div></div>
  </aside>
  <div className="app-content"><header className="topbar"><div className="breadcrumb">Workspace <span>/</span> <strong>Opportunity engine</strong></div><div className="topbar-actions"><span className={`connection ${connected ? 'connected' : ''}`} data-testid="connection-status"><i/>{connected ? 'Live data connected' : 'Connecting to feed'}</span><span className="topbar-divider"/><span className="paper-badge" data-testid="paper-mode-badge"><FlaskConical size={13}/> PAPER MODE</span><span className="locked" title="Real orders are locked. No signing credentials configured." data-testid="live-locked"><LockKeyhole size={14}/> Live locked</span><span className="avatar">P</span></div></header>
    <main>{children}</main><footer className="footer"><span><AudioLines size={13}/> Polymarket public market data</span><span data-testid="footer-mode">Paper execution only <span>·</span> Two-leg orders are not atomic</span><span className="footer-wallet" data-testid="paper-wallet-balance"><Wallet size={13}/> {money(data?.wallet?.cash ?? 1000)} paper cash</span></footer>
  </div>
</div>;