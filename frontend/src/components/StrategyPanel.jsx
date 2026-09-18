import { useEffect, useState } from 'react';
import { Check, ChevronDown, LockKeyhole, ShieldCheck, SlidersHorizontal, Wallet } from 'lucide-react';
import { Button } from './ui/button';
import { Input } from './ui/input';

export const StrategyPanel = ({settings, policy, save, saving}) => {
  const [form,setForm]=useState(settings),[advanced,setAdvanced]=useState(false);
  useEffect(()=>setForm(settings),[settings]);
  if(!form)return <aside className="strategy-panel skeleton"/>;
  const change=(key,value)=>setForm(f=>({...f,[key]:value}));
  const dirty=JSON.stringify(form)!==JSON.stringify(settings);
  return <aside className="strategy-panel"><form onSubmit={e=>{e.preventDefault();save({...form,budget:Number(form.budget)});}}><div className="section-heading"><span><SlidersHorizontal size={15}/> Strict arbitrage</span><span className="tiny-badge">LOCKED</span></div>
    <label className="field-label" htmlFor="budget-pool">Budget pool <Wallet size={13}/></label><div className="money-input"><span>$</span><Input id="budget-pool" data-testid="budget-pool-input" type="number" min="0" max="100000" step="0.01" required value={form.budget} onChange={e=>change('budget',e.target.value)}/><span>USD</span></div><div className="quick-values">{[25,50,100,250].map(n=><button type="button" key={n} data-testid={`budget-preset-${n}`} className={Number(form.budget)===n?'chosen':''} onClick={()=>change('budget',n)}>${n}</button>)}</div>
    <div className="form-divider"/><div className="strict-policy" data-testid="strict-policy"><div className="field-label"><span><ShieldCheck size={13}/> UP = DOWN = Q</span><LockKeyhole size={12}/></div><div><span>Profit floor</span><b data-testid="strict-profit-floor">${policy?.profit_floor ?? '0.10'}</b></div><div><span>Safety margin</span><b data-testid="strict-safety-margin">${policy?.safety_margin ?? '0.02'}</b></div><div className="strict-required"><span>Required net profit</span><b data-testid="strict-required-profit">${policy?.required_profit ?? '0.12'}</b></div><div><span>Minimum combined cost</span><b data-testid="strict-minimum-cost">${policy?.minimum_total_cost ?? '1.00'}</b></div><p>Smallest qualifying Q · Fees included</p></div>
    <button type="button" className="advanced-toggle" data-testid="advanced-constraints-toggle" onClick={()=>setAdvanced(!advanced)}>Capital limit <ChevronDown size={14} className={advanced?'rotated':''}/></button>{advanced&&<div className="advanced-fields"><label>Maximum deployment ($)<Input data-testid="max-deployment-input" type="number" min="0" max="100000" step="0.01" placeholder="Up to budget pool" value={form.max_deployment??''} onChange={e=>change('max_deployment',e.target.value===''?null:Number(e.target.value))}/></label></div>}
    <Button type="submit" data-testid="apply-strategy-button" className="apply-button" disabled={saving||!dirty}>{saving?'Applying…':dirty?'Apply parameters':<><Check size={14}/> Parameters applied</>}</Button>
  </form></aside>;
};