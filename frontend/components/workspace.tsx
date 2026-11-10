'use client';
import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { AUTH_CHANGED, readSession, request, saveSession, setToken } from '@/lib/api';
import type { LoginResponse, User } from '@/lib/contracts';
import { Feedback, useAction } from './api-ui';
import { Clients, ClientDetails } from './clients';
import { Imports, Users } from './management';

export default function Workspace() {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<{ user: User } | null>(null);
  const [ready, setReady] = useState(false);
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const action = useAction();
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const sync = () => {
      clearTimeout(timer);
      const saved = readSession();
      setSession(saved); setReady(true);
      if (saved) timer = setTimeout(() => setToken(null), Math.min(saved.expiresAt - Date.now(), 2147483647));
    };
    // Storage is read after hydration, and changes are shared between tabs.
    void Promise.resolve().then(sync);
    window.addEventListener(AUTH_CHANGED, sync); window.addEventListener('storage', sync);
    return () => { clearTimeout(timer); window.removeEventListener(AUTH_CHANGED, sync); window.removeEventListener('storage', sync); };
  }, []);
  if (!ready) return <main className="auth-loading" aria-label="Загрузка" />;
  if (!session) return <main className="login-page"><form className="login-card" onSubmit={e => { e.preventDefault(); void action.run(async () => {
    const data = await request<LoginResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ login, password }) });
    saveSession(data); setPassword(''); router.replace('/');
  }); }}><h1>Контур КЦР</h1><p>Контроль передачи оригиналов документов</p><label>Логин<input name="login" required autoComplete="username" value={login} onChange={e => setLogin(e.target.value)} /></label><label>Пароль<input name="password" required autoComplete="current-password" type="password" value={password} onChange={e => setPassword(e.target.value)} /></label><Feedback error={action.error} /><button className="button primary" disabled={action.busy}>Войти</button></form></main>;
  const admin = session.user.role === 'ADMIN';
  const match = pathname.match(/^\/(client|call)\/([^/]+)$/);
  let code = '';
  try { code = match ? decodeURIComponent(match[2]) : ''; } catch { /* Invalid URL is shown as an unknown page. */ }
  const links = [['/', 'Клиенты'], ...(admin ? [['/upload', 'Загрузка реестра'], ['/import-report', 'Отчёт об импорте'], ['/users', 'Пользователи']] : [])];
  const restricted = ['/upload', '/import-report', '/users'].includes(pathname) && !admin;
  return <main className="app-shell"><aside className="sidebar"><div className="brand"><span className="brand-mark">К</span><span><b>Контур КЦР</b><small>{session.user.login} · {admin ? 'Администратор' : 'Оператор'}</small></span></div>{links.map(([href, title]) => <button key={href} className={`nav-item ${pathname === href ? 'selected' : ''}`} aria-label={title} title={title} onClick={() => router.push(href)}><span className="nav-icon" aria-hidden="true">{({ '/': '▦', '/upload': '⇧', '/import-report': '☷', '/users': '♙' } as Record<string, string>)[href]}</span>{title}</button>)}<button className="logout" onClick={() => { setToken(null); router.replace('/'); }}>Выйти</button></aside><section className="workspace"><header className="topbar">Контур КЦР</header><div className="content" key={`${session.user.id}:${pathname}`}>
    {restricted ? <p role="alert">Раздел доступен только администратору.</p> : code ? <ClientDetails code={code} user={session.user} call={match?.[1] === 'call'} /> : pathname === '/' || pathname === '/today' ? <Clients /> : pathname === '/users' ? <Users /> : pathname === '/upload' || pathname === '/import-report' ? <Imports reportOnly={pathname === '/import-report'} /> : <><h1>Раздел недоступен</h1><button className="button secondary" onClick={() => router.push('/')}>К клиентам</button></>}
  </div></section></main>;
}
