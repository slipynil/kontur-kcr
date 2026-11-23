'use client';
import { AlertCircle, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight, LoaderCircle } from 'lucide-react';
import { Popover } from 'radix-ui';
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
export function Feedback({ error, loading, loadingText = 'Загрузка…', notice }: {
  error?: string; loading?: boolean; loadingText?: string; notice?: string;
}) {
  return <>{loading && <p className="feedback feedback-info" role="status">
    <LoaderCircle className="feedback-spinner" size={16} aria-hidden="true" />{loadingText}
  </p>}
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
export function QuarterInput({ label, value, onChange, required = false, ariaLabel = 'Квартал' }: {
  label: string; value: string; onChange: (value: string) => void; required?: boolean; ariaLabel?: string;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const anchor = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const listId = `${id}-quarters`;
  const activeId = open && active >= 0 ? `${listId}-${active}` : undefined;
  const now = currentQuarter();
  const quarters = Array.from({ length: 13 }, (_, index) => quarterOffset(now, 4 - index));
  if (quarterPattern.test(value) && !quarters.includes(value)) quarters.unshift(value);

  useEffect(() => {
    if (activeId) document.getElementById(activeId)?.scrollIntoView?.({ block: 'nearest' });
  }, [activeId]);

  function changeOpen(next: boolean) {
    setOpen(next);
    setActive(-1);
  }

  function choose(quarter: string) {
    onChange(quarter);
    changeOpen(false);
    input.current?.focus();
  }

  return <div className="quarter-field">
    <label htmlFor={id}>{label}</label>
    <Popover.Root open={open} onOpenChange={changeOpen}>
      <Popover.Anchor asChild>
        <div ref={anchor} className="quarter-control">
          <input ref={input} id={id} name="quarter" role="combobox" aria-label={ariaLabel} aria-expanded={open}
            aria-controls={open ? listId : undefined} aria-activedescendant={activeId} aria-autocomplete="none"
            autoComplete="off" autoCapitalize="off" spellCheck={false}
            placeholder={now} pattern="[0-9]{4}_Q[1-4]" required={required} value={value}
            onClick={() => changeOpen(true)}
            onChange={event => { onChange(event.target.value); changeOpen(false); }}
            onKeyDown={event => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                event.preventDefault();
                setOpen(true);
                const step = event.key === 'ArrowDown' ? 1 : -1;
                const start = active < 0 && step < 0 ? quarters.length : active;
                setActive(Math.max(0, Math.min(quarters.length - 1, start + step)));
              } else if (open && event.key === 'Enter' && active >= 0) {
                event.preventDefault();
                choose(quarters[active]);
              } else if (open && event.key === 'Tab') {
                changeOpen(false);
              }
            }} />
          <button type="button" tabIndex={-1} aria-label={`${label}: выбрать из списка`}
            onClick={() => { input.current?.focus(); changeOpen(!open); }}>
            <ChevronDown size={14} aria-hidden="true" />
          </button>
        </div>
      </Popover.Anchor>
      <Popover.Portal>
        <Popover.Content id={listId} role="listbox" aria-label={label} className="quarter-options"
          align="start" sideOffset={4} collisionPadding={8}
          onOpenAutoFocus={event => event.preventDefault()}
          onCloseAutoFocus={event => event.preventDefault()}
          onInteractOutside={event => { if (anchor.current?.contains(event.target as Node)) event.preventDefault(); }}>
          {quarters.map((quarter, index) => <div id={`${listId}-${index}`} key={quarter} role="option"
            aria-selected={quarter === value} data-active={index === active}
            onMouseDown={event => event.preventDefault()} onClick={() => choose(quarter)}>
            {quarter}
            {quarter === value && <Check size={14} aria-hidden="true" />}
          </div>)}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  </div>;
}
