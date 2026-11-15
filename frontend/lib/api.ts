import type { LoginResponse } from './contracts';
const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? 'http://176.109.100.140').replace(/\/+$/, '');
const TOKEN_KEY = 'kontur-api-token';
const SESSION_KEY = 'kontur-api-session';
export const AUTH_CHANGED = 'kontur-auth-changed';
export function getToken() {
  return typeof window === 'undefined' ? null : window.localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string | null) {
  if (typeof window === 'undefined') return;
  if (token) window.localStorage.setItem(TOKEN_KEY, token);
  else { window.localStorage.removeItem(TOKEN_KEY); window.localStorage.removeItem(SESSION_KEY); }
  window.dispatchEvent(new Event(AUTH_CHANGED));
}
export function saveSession(session: LoginResponse) {
  window.localStorage.setItem(SESSION_KEY, JSON.stringify({ user: session.user, expiresAt: Date.now() + session.expires_in * 1000 }));
  setToken(session.token);
}
export function readSession(): { user: LoginResponse['user']; expiresAt: number } | null {
  try {
    const session = JSON.parse(window.localStorage.getItem(SESSION_KEY) || 'null');
    return getToken() && session?.expiresAt > Date.now() && ['ADMIN', 'OPERATOR'].includes(session.user?.role) ? session : null;
  } catch { return null; }
}
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export function apiError(data: unknown, fallback: string) {
  return data && typeof data === 'object' && 'message' in data && typeof data.message === 'string' ? data.message : fallback;
}
export async function api(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token && path !== '/auth/login') headers.set('Authorization', `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`${API_BASE}/api/v1${path}`, { ...init, headers, cache: 'no-store' });
  if (response.status === 401 && token === getToken()) setToken(null);
  return response;
}
export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await api(path, init);
  // Public DELETE operations return 200 without a response schema or body.
  if (response.ok && init.method?.toUpperCase() === 'DELETE') return undefined as T;
  const data: unknown = response.status === 204 ? undefined : await response.json().catch(() => undefined);
  if (!response.ok) {
    const code = data && typeof data === 'object' && 'code' in data ? String(data.code) : 'HTTP_ERROR';
    throw new ApiError(response.status, code, apiError(data, `Ошибка запроса (${response.status})`));
  }
  if (data === undefined && response.status !== 204) throw new Error('Сервер вернул некорректный ответ');
  return data as T;
}
export function queryString(values: Record<string, string | number | undefined>) {
  const query = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== '') query.set(key, String(value)); });
  return `?${query}`;
}
