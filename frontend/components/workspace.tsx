'use client';

import { FileText, LayoutGrid, LogOut, Upload, UserRound, UsersRound } from 'lucide-react';
import { useEffect, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { AUTH_CHANGED, readSession, setToken } from '@/lib/api';
import type { User } from '@/lib/contracts';
import { Clients } from './clients';
import { ClientDetails } from './client-details';
import { Imports, Users } from './management';
import { ThemeToggle } from './theme-toggle';
import { Login } from './login';
import { Brand, EmptyState, PageHeader } from './presentation';

const adminLinks = [
  { href: '/upload', label: 'Загрузка реестра', Icon: Upload },
  { href: '/import-report', label: 'Отчёт об импорте', Icon: FileText },
  { href: '/users', label: 'Пользователи', Icon: UsersRound },
];

export default function Workspace() {
  const router = useRouter();
  const pathname = usePathname();
  const [session, setSession] = useState<{ user: User } | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const sync = () => {
      clearTimeout(timer);
      const saved = readSession();
      setSession(saved); setReady(true);
      if (saved) timer = setTimeout(() => setToken(null), Math.min(saved.expiresAt - Date.now(), 2147483647));
    };
    // Read storage after hydration and follow session changes in other tabs.
    void Promise.resolve().then(sync);
    window.addEventListener(AUTH_CHANGED, sync);
    window.addEventListener('storage', sync);
    return () => {
      clearTimeout(timer);
      window.removeEventListener(AUTH_CHANGED, sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  if (!ready) return <main className="auth-loading" aria-label="Загрузка" />;
  if (!session) return <Login />;

  const { user } = session;
  const admin = user.role === 'ADMIN';
  const match = pathname.match(/^\/(client|call)\/([^/]+)$/);
  let code = '';
  try { code = match ? decodeURIComponent(match[2]) : ''; } catch { /* An invalid URL falls through to the unknown page. */ }
  const registry = pathname === '/' || pathname === '/today';
  const restricted = !admin && adminLinks.some(link => link.href === pathname);
  const links = [{ href: '/', label: 'Клиенты', Icon: LayoutGrid }, ...(admin ? adminLinks : [])];
  const navigation = <nav aria-label="Главное меню">
    {links.map(({ href, label, Icon }) => <Link key={href} href={href} className="nav-item"
      aria-current={(href === '/' ? registry || !!code : pathname === href) ? 'page' : undefined}>
      <Icon aria-hidden="true" />{label}
    </Link>)}
  </nav>;
  const sessionActions = <>
    <ThemeToggle />
    <button className="button text-link" type="button" onClick={() => { setToken(null); router.replace('/'); }}><LogOut aria-hidden="true" />Выйти</button>
  </>;
  const side = <aside className="side-rail">
    <Brand />
    {navigation}
    <footer className="side-footer">
      <div className="account"><UserRound size={16} aria-hidden="true" /><div>
        <strong>{admin ? 'Администратор' : 'Оператор'}</strong><small>{user.login}</small>
      </div></div>
      <div className="side-actions">{sessionActions}</div>
    </footer>
  </aside>;
  const top = <div className="top-navigation">{navigation}<div className="top-actions">{sessionActions}</div></div>;

  return registry ? <Clients key={`${user.id}:${pathname}`} user={user} side={side} top={top} /> :
      <div className="app-shell">
        {side}
        <main className="workspace-body" key={`${user.id}:${pathname}`}>
          {restricted ? <>
            <PageHeader title="Доступ ограничен" description="Рабочая область оператора" />
            <div className="access-state"><EmptyState title="Раздел доступен только администратору.">
              <button type="button" className="button primary" onClick={() => router.push('/')}>К клиентам</button>
            </EmptyState></div>
          </> : code ? <ClientDetails code={code} user={user} call={match?.[1] === 'call'} />
            : pathname === '/users' ? <Users />
            : pathname === '/upload' || pathname === '/import-report' ? <Imports reportOnly={pathname === '/import-report'} />
            : <><PageHeader title="Раздел недоступен" /><div className="access-state"><EmptyState title="Страница не найдена">
              <button type="button" className="button primary" onClick={() => router.push('/')}>К клиентам</button>
            </EmptyState></div></>}
        </main>
      </div>;
}
