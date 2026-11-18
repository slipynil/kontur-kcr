'use client';
import { useEffect, useRef, useState } from 'react';
import { request } from '@/lib/api';

export function message(error: unknown) { return error instanceof Error ? error.message : 'Не удалось выполнить запрос'; }
export function useResource<T>(path: string | null, revision = 0, poll = false, refreshMs = 0) {
  const [state, setState] = useState<{ key: string; path?: string; data?: T; error?: string }>({ key: '' });
  const key = `${path}:${revision}`;
  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    let inFlight = false;
    const scheduleRefresh = () => {
      if (!controller.signal.aborted && document.visibilityState !== 'hidden') {
        timer = setTimeout(load, refreshMs);
      }
    };
    const load = async () => {
      if (inFlight || controller.signal.aborted) return;
      inFlight = true;
      try {
        const data = await request<T>(path, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setState({ key, path, data });
          if (!refreshMs && poll && (data as { status?: string }).status === 'PROCESSING') timer = setTimeout(load, 2000);
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setState(previous => ({
            key, path, error: message(error),
            data: refreshMs && previous.key === key ? previous.data : undefined,
          }));
        }
      } finally {
        inFlight = false;
        if (refreshMs) scheduleRefresh();
      }
    };
    const onVisibilityChange = () => {
      clearTimeout(timer);
      if (document.visibilityState !== 'hidden') void load();
    };
    if (refreshMs) document.addEventListener('visibilitychange', onVisibilityChange);
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
      if (refreshMs) document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [path, key, poll, refreshMs]);
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
  const lastPage = Math.max(1, Math.ceil(total / 20));
  return <nav className="pagination" aria-label="Страницы результатов">
    <button className="button secondary" disabled={page <= 1} onClick={() => onChange(page - 1)}>Назад</button>
    <span>Страница {page} из {lastPage}<small className="pagination-total">Всего записей: {total.toLocaleString('ru-RU')}</small></span>
    <button className="button secondary" disabled={page >= lastPage} onClick={() => onChange(page + 1)}>Далее</button>
  </nav>;
}
export const stages = { POOL: 'Пул', IN_WORK: 'В работе', WAIT_DEADLINE: 'Ожидание срока', CLAIM: 'Претензия', CLOSED: 'Цикл завершён' };
export const dynamics = { ALL_SUBMITTED: 'Все переданы', REDUCING: 'Уменьшается', NO_CHANGE: 'Без изменений', GROWING: 'Растёт' };
export const durations = { FIRST_TIME: 'Впервые', REPEATED: 'Повторно', LONG_DELAY_2Q: 'Длительная задержка' };
export function businessDate() { return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow' }).format(new Date()); }
export function currentQuarter() { const date = businessDate(); return `${date.slice(0, 4)}_Q${Math.ceil(Number(date.slice(5, 7)) / 3)}`; }
export function QuarterInput({ value, onChange, required = false, ariaLabel = 'Квартал' }: { value: string; onChange: (value: string) => void; required?: boolean; ariaLabel?: string }) {
  return <input aria-label={ariaLabel} placeholder="2026_Q4" pattern="[0-9]{4}_Q[1-4]" required={required} value={value} onChange={e => onChange(e.target.value)} />;
}
