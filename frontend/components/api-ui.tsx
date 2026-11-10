'use client';
import { useEffect, useRef, useState } from 'react';
import { request } from '@/lib/api';

export function message(error: unknown) { return error instanceof Error ? error.message : 'Не удалось выполнить запрос'; }
export function useResource<T>(path: string | null, revision = 0, poll = false) {
  const [state, setState] = useState<{ key: string; path?: string; data?: T; error?: string }>({ key: '' });
  const key = `${path}:${revision}`;
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const data = await request<T>(path, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setState({ key, path, data });
          if (poll && (data as { status?: string }).status === 'PROCESSING') timer = setTimeout(load, 2000);
        }
      } catch (error) {
        if (!controller.signal.aborted) setState({ key, path, error: message(error) });
      }
    };
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, key, poll]);
  return { data: state.path === path ? state.data : undefined, error: state.key === key ? state.error : undefined, loading: !!path && state.key !== key };
}
export function useAction() {
  const locked = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  async function run(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true; setBusy(true); setError(''); setNotice('');
    try { await action(); } catch (error) { setError(message(error)); }
    finally { locked.current = false; setBusy(false); }
  }
  return { busy, error, notice, setNotice, run };
}
export function Feedback({ error, loading, notice }: { error?: string; loading?: boolean; notice?: string }) {
  return <>{loading && <p role="status">Загрузка…</p>}{error && <p className="form-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}</>;
}
export function Pager({ page, total, onChange }: { page: number; total: number; onChange: (page: number) => void }) {
  return <div className="pagination"><button className="button secondary" disabled={page <= 1} onClick={() => onChange(page - 1)}>Назад</button><span>Страница {page} · Всего {total}</span><button className="button secondary" disabled={page * 20 >= total} onClick={() => onChange(page + 1)}>Далее</button></div>;
}
export const stages = { POOL: 'Пул', IN_WORK: 'В работе', WAIT_DEADLINE: 'Ожидание срока', CLAIM: 'Претензия', CLOSED: 'Цикл завершён' };
export const dynamics = { ALL_SUBMITTED: 'Все переданы', REDUCING: 'Уменьшается', NO_CHANGE: 'Без изменений', GROWING: 'Растёт' };
export const durations = { FIRST_TIME: 'Впервые', REPEATED: 'Повторно', LONG_DELAY_2Q: 'Длительная задержка' };
export function businessDate() { return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow' }).format(new Date()); }
export function currentQuarter() { const date = businessDate(); return `${date.slice(0, 4)}_Q${Math.ceil(Number(date.slice(5, 7)) / 3)}`; }
export function QuarterInput({ value, onChange, required = false }: { value: string; onChange: (value: string) => void; required?: boolean }) {
  return <input aria-label="Квартал" placeholder="2026_Q4" pattern="[0-9]{4}_Q[1-4]" required={required} value={value} onChange={e => onChange(e.target.value)} />;
}
