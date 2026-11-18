'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { LockKeyhole, UserRound } from 'lucide-react';
import { request, saveSession } from '@/lib/api';
import type { LoginResponse } from '@/lib/contracts';
import { Feedback, useAction } from './api-ui';
import { Brand } from './presentation';

export function Login() {
  const router = useRouter();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const action = useAction();

  return <main className="login-page">
    <header className="login-brand"><Brand /></header>
    <div className="login-stage">
      <section className="login-card" aria-labelledby="login-title">
        <h1 id="login-title">Вход</h1>
        <p className="muted">Войдите в рабочую область «Контур КЦР»</p>
        <form className="stack" noValidate onSubmit={event => {
          event.preventDefault();
          void action.run(async () => {
            if (!login.trim()) throw new Error('Введите логин');
            if (!password) throw new Error('Введите пароль');
            const data = await request<LoginResponse>('/auth/login', {
              method: 'POST', body: JSON.stringify({ login, password }),
            });
            saveSession(data); setPassword(''); router.replace('/');
          });
        }}>
          <label>Логин<span className="input-icon"><input name="login" placeholder="Введите логин" required autoComplete="username" autoCapitalize="none" spellCheck={false} value={login} onChange={event => setLogin(event.target.value)} /><UserRound size={16} aria-hidden="true" /></span></label>
          <label>Пароль<span className="input-icon"><input name="password" placeholder="Введите пароль" required type="password" autoComplete="current-password" value={password} onChange={event => setPassword(event.target.value)} /><LockKeyhole size={16} aria-hidden="true" /></span></label>
          <Feedback error={action.error} />
          <button className="button primary" disabled={action.busy}>Войти</button>
        </form>
        <p className="login-hint">Доступ выдаёт администратор</p>
      </section>
    </div>
  </main>;
}
