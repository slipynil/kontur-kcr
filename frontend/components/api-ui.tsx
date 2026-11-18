'use client';
import { AlertCircle, CheckCircle2, ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
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
  function reset() { setError(''); setNotice(''); }
  return { busy, error, notice, setNotice, run, reset };
}
export function Feedback({ error, loading, notice }: { error?: string; loading?: boolean; notice?: string }) {
  return <>{loading && <p className="feedback feedback-info" role="status"><LoaderCircle size={16} aria-hidden="true" />Загрузка…</p>}
    {error && <p className="feedback feedback-bad" role="alert"><AlertCircle size={16} aria-hidden="true" />{error}</p>}
    {notice && <p className="feedback feedback-good" role="status"><CheckCircle2 size={16} aria-hidden="true" />{notice}</p>}</>;
}

export function Pager({ page, total, pageSize = 20, onChange }: {
  page: number; total: number; pageSize?: number; onChange: (page: number) => void;
}) {
  if (!total) return null;
  const lastPage = Math.max(1, Math.ceil(total / pageSize));
  const start = Math.max(1, Math.min(page - 1, lastPage - 2));
  const items = Array.from({ length: Math.min(3, lastPage) }, (_, index) => start + index);
  const number = (value: number) => value.toLocaleString('ru-RU');
  return <nav className="pagination" aria-label="Страницы результатов">
    <span>Показано {number(Math.min((page - 1) * pageSize + 1, total))}–{number(Math.min(page * pageSize, total))} из {number(total)}</span>
    <div className="inline-actions">
      <button type="button" className="button text-link" aria-label="Назад" disabled={page <= 1} onClick={() => onChange(page - 1)}><ChevronLeft aria-hidden="true" /></button>
      {items.map(value => <button key={value} type="button" className="button text-link page-button" aria-label={`Страница ${value}`}
        aria-current={page === value ? 'page' : undefined} onClick={() => onChange(value)}>{value}</button>)}
      <button type="button" className="button text-link" aria-label="Далее" disabled={page >= lastPage} onClick={() => onChange(page + 1)}><ChevronRight aria-hidden="true" /></button>
    </div>
  </nav>;
}

export const stages = { POOL: 'Пул', IN_WORK: 'В работе', WAIT_DEADLINE: 'Ожидание срока', CLAIM: 'Претензия', CLOSED: 'Цикл завершён' };
export const dynamics = { ALL_SUBMITTED: 'Все переданы', REDUCING: 'Уменьшается', NO_CHANGE: 'Без изменений', GROWING: 'Растёт' };
export const durations = { FIRST_TIME: 'Впервые', REPEATED: 'Повторно', LONG_DELAY_2Q: 'Длительная задержка' };
export const tags = { VIP: 'VIP', NOVICE: 'Новичок', INTEGRATOR: 'Интегратор', TECH_PARTNER: 'Техпартнёр', MPP: 'МПП', DO_NOT_WORK: 'Не работать' };
export const quarterPattern = /^[0-9]{4}_Q[1-4]$/;
export function businessDate() { return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Moscow' }).format(new Date()); }
export function currentQuarter() { const date = businessDate(); return `${date.slice(0, 4)}_Q${Math.ceil(Number(date.slice(5, 7)) / 3)}`; }
export function quarterOffset(quarter: string, shift: number) {
  const index = Number(quarter.slice(0, 4)) * 4 + Number(quarter.at(-1)) - 1 + shift;
  return `${Math.floor(index / 4)}_Q${index % 4 + 1}`;
}
export function quarterName(value?: string | null) {
  if (!value || !quarterPattern.test(value)) return value || '—';
  return `${['I', 'II', 'III', 'IV'][Number(value.at(-1)) - 1]} квартал ${value.slice(0, 4)}`;
}
export function displayDate(value?: string | null) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date.toLocaleDateString('ru-RU', {
    day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Europe/Moscow',
  }) : '—';
}
export function businessTime(value: string) {
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.toLocaleString('ru-RU', {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow',
  }) : '—';
}
export function QuarterInput({ value, onChange, required = false, ariaLabel = 'Квартал' }: {
  value: string; onChange: (value: string) => void; required?: boolean; ariaLabel?: string;
}) {
  const id = useId();
  const now = currentQuarter();
  return <><input aria-label={ariaLabel} placeholder={now} pattern="[0-9]{4}_Q[1-4]" list={id}
    required={required} value={value} onChange={event => onChange(event.target.value)} />
    <datalist id={id}>{Array.from({ length: 13 }, (_, index) => quarterOffset(now, 4 - index)).map(quarter =>
      <option key={quarter} value={quarter}>{quarterName(quarter)}</option>,
    )}</datalist></>;
}
