import axios from 'axios';
let session = localStorage.getItem('parity-session');
if (!session) { session = crypto.randomUUID(); localStorage.setItem('parity-session', session); }
export const sessionId = session;
export const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
export const api = axios.create({ baseURL: API, headers: { 'X-Session-ID': session }, timeout: 20000 });
export const money = (value = 0, digits = 2) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value);
export const signed = (value = 0) => `${value > 0 ? '+' : value < 0 ? '−' : ''}${money(Math.abs(value))}`;
export const num = (value = 0, digits = 2) => Number(value).toLocaleString('en-US', { maximumFractionDigits: digits, minimumFractionDigits: digits });
export const errorText = error => typeof error.response?.data?.detail === 'string' ? error.response.data.detail : 'Unable to save. Check your values and connection.';
export const objectives = [
  { id: 'maximum_upside', label: 'Maximum upside', short: 'Maximize best-case profit', icon: 'up' },
  { id: 'guaranteed_profit', label: 'Guaranteed profit', short: 'Profit in either outcome', icon: 'shield' },
  { id: 'controlled_loss', label: 'Controlled loss', short: 'Upside with a loss ceiling', icon: 'sliders' },
  { id: 'max_worst_case', label: 'Maximum worst-case', short: 'Strongest balanced payoff', icon: 'scale' },
  { id: 'custom', label: 'Custom objective', short: 'Your allocation constraints', icon: 'settings' }
];