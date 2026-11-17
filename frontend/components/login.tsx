'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Onest } from 'next/font/google';
import { request, saveSession } from '@/lib/api';
import type { LoginResponse } from '@/lib/contracts';
import { Feedback, useAction } from './api-ui';

// algocode.io uses Onest; the font is downloaded at build time and self-hosted.
const onest = Onest({ subsets: ['latin', 'cyrillic'], display: 'swap' });

export function Login() {
  const router = useRouter();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const action = useAction();
  return <main className={`login-page ${onest.className}`}>
    <div className="login-card">
      <div className="login-logo"><b>Контур КЦР</b></div>
      {/* No self-service registration: accounts are created by an administrator. */}
      <div className="login-tabs"><span className="login-tab active" aria-current="page">Вход</span></div>
      {/* Native required bubbles are invisible to the a11y tree and headless runs
          (management.tsx:37-38): both fields validate here and render via Feedback. */}
      <form className="login-form" noValidate onSubmit={e => { e.preventDefault(); void action.run(async () => {
        if (!login.trim()) throw new Error('Введите логин');
        if (!password) throw new Error('Введите пароль');
        const data = await request<LoginResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ login, password }) });
        saveSession(data); setPassword(''); router.replace('/');
      }); }}>
        <fieldset className="login-fields">
          <label className="login-field">
            <svg aria-hidden="true" viewBox="0 0 20 20" fill="none"><path stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" d="M10 12.5a5 5 0 1 0 0-10 5 5 0 0 0 0 10" /><path stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" d="M2.422 16.875a8.75 8.75 0 0 1 15.156 0" /></svg>
            <span className="login-field-label">Логин</span>
            <input name="login" placeholder="Логин" required autoComplete="username" value={login} onChange={e => setLogin(e.target.value)} />
          </label>
          <label className="login-field">
            <svg aria-hidden="true" viewBox="0 0 22 23" fill="none"><path fill="currentColor" fillRule="evenodd" clipRule="evenodd" d="M11 2.5c-3.684 0-5.25 3.151-5.25 6-.956 0-1.75.777-1.75 1.714v8.572c0 .937.794 1.714 1.75 1.714h10.5c.956 0 1.75-.777 1.75-1.714v-8.572c0-.937-.794-1.714-1.75-1.714 0-2.849-1.566-6-5.25-6m0 1.451c2.67 0 3.626 2.598 3.626 4.549H7.404c0-1.951.926-4.549 3.596-4.549m-5.484 7.046a1 1 0 0 1 1-1h9.033a1 1 0 0 1 1 1v7.044a1 1 0 0 1-1 1H6.516a1 1 0 0 1-1-1z" /></svg>
            <span className="login-field-label">Пароль</span>
            <input name="password" placeholder="Пароль" required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} />
          </label>
        </fieldset>
        <Feedback error={action.error} />
        <button className="login-submit" disabled={action.busy}>Войти</button>
        <p className="login-hint">Доступ выдаёт администратор</p>
      </form>
    </div>
  </main>;
}
