import { useEffect, useState } from 'react';
import { ArrowUpRight, Check, ChevronDown, Scale, Settings2, ShieldCheck, SlidersHorizontal, Wallet } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { objectives } from '../lib/api';

const icons = { up: ArrowUpRight, shield: ShieldCheck, sliders: SlidersHorizontal, scale: Scale, settings: Settings2 };
export const StrategyPanel = ({ settings, save, saving }) => {
  const [form, setForm] = useState(settings);
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => setForm(settings), [settings]);
  if (!form) return <aside className="strategy-panel skeleton"/>;
  const change = (key, value) => setForm(prev => ({...prev, [key]: value}));
  const submit = e => { e.preventDefault(); save({...form, budget: Number(form.budget), max_loss: Number(form.max_loss), max_deployment: form.max_deployment === '' ? null : form.max_deployment}); };
  const dirty = JSON.stringify(form) !== JSON.stringify(settings);
  return <aside className="strategy-panel"><form onSubmit={submit}><div className="section-heading"><span><SlidersHorizontal size={15}/> Strategy parameters</span><span className="tiny-badge">AUTO</span></div>
    <label className="field-label" htmlFor="budget-pool">Budget pool <Wallet size={13}/></label><div className="money-input"><span>$</span><Input id="budget-pool" type="number" min="0" max="100000" step="0.01" required data-testid="budget-pool-input" value={form.budget} onChange={e => change('budget', e.target.value)}/><span>USD</span></div>
    <div className="quick-values">{[25, 50, 100, 250].map(n => <button key={n} type="button" data-testid={`budget-preset-${n}`} className={Number(form.budget) === n ? 'chosen' : ''} onClick={() => change('budget', n)}>${n}</button>)}</div>
    <div className="form-divider"/><div className="field-label">Optimization objective</div><div className="objectives">{objectives.map(o => { const Icon = icons[o.icon]; return <button type="button" key={o.id} data-testid={`objective-${o.id.replaceAll('_','-')}`} className={`objective ${form.objective === o.id ? 'chosen' : ''}`} onClick={() => change('objective', o.id)}><Icon size={16}/><span>{o.label}<small>{o.short}</small></span><span className="radio-dot">{form.objective === o.id && <i/>}</span></button>; })}</div>
    <div className="form-divider"/><div className="field-label"><label htmlFor="max-loss">Maximum acceptable loss</label><span className="amber">RISK LIMIT</span></div><div className="money-input loss"><span>$</span><Input id="max-loss" type="number" min="0" max="100000" step="0.01" required data-testid="max-loss-input" value={form.max_loss} onChange={e => change('max_loss', e.target.value)}/><ShieldCheck size={15}/></div>
    <button type="button" className="advanced-toggle" data-testid="advanced-constraints-toggle" onClick={() => setAdvanced(!advanced)}>Advanced constraints <ChevronDown size={14} className={advanced ? 'rotated' : ''}/></button>
    {(advanced || form.objective === 'custom') && <div className="advanced-fields"><label>Maximum deployment ($)<Input data-testid="max-deployment-input" type="number" min="0" max="100000" step="0.01" placeholder="Up to budget pool" value={form.max_deployment ?? ''} onChange={e => change('max_deployment', e.target.value === '' ? null : Number(e.target.value))}/></label><label>Minimum profit ($)<Input data-testid="min-profit-input" type="number" min="0.00001" step="0.00001" value={form.min_profit} onChange={e => change('min_profit', Number(e.target.value))}/></label>{form.objective === 'custom' && <><label>Custom objective<select data-testid="custom-objective-select" value={form.custom_objective} onChange={e => change('custom_objective', e.target.value)}>{objectives.filter(o => ['maximum_upside','guaranteed_profit','max_worst_case'].includes(o.id)).map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select></label><label>Minimum UP share allocation (%)<Input data-testid="min-up-allocation-input" type="number" min="0" max="100" value={form.min_up_share_pct} onChange={e => change('min_up_share_pct', Number(e.target.value))}/></label><label>Maximum UP share allocation (%)<Input data-testid="max-up-allocation-input" type="number" min="0" max="100" value={form.max_up_share_pct} onChange={e => change('max_up_share_pct', Number(e.target.value))}/></label></>}</div>}
    <Button data-testid="apply-strategy-button" className="apply-button" type="submit" disabled={saving || !dirty}>{saving ? 'Applying…' : dirty ? 'Apply parameters' : <><Check size={14}/> Parameters applied</>}</Button>
  </form></aside>;
};