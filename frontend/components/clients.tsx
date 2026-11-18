'use client';
import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { newRequestId, queryString, request } from '@/lib/api';
import type { ClientCard, ClientListResponse, EventListResponse, SnapshotHistoryResponse, TakeResponse, User, TakeRequest, ReassignmentRequest, PoolPublicationRequest } from '@/lib/contracts';
import { EventForm } from './event-form';
import { businessDate, dynamics, durations, Feedback, Pager, QuarterInput, stages, useAction, useResource } from './api-ui';

const QUARTER_PATTERN = /^[0-9]{4}_Q[1-4]$/;
const REGISTRY_PAGE_SIZE = 100;
const PUBLICATION_BATCH_SIZE = 1000;

export function Clients({ user }: { user: User }) {
  const router = useRouter();
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [publishQuarter, setPublishQuarter] = useState('');
  const publishAction = useAction();
  const search = (draft.search ?? '').trim();
  const appliedSearch = filters.search ?? '';
  useEffect(() => {
    if (search === appliedSearch) return;
    const timer = setTimeout(() => {
      setFilters(current => ({ ...current, search }));
      setPage(1);
    }, 300);
    return () => clearTimeout(timer);
  }, [search, appliedSearch]);
  const result = useResource<ClientListResponse>('/clients' + queryString({ ...filters, page, page_size: 20 }), revision);
  const change = (name: string, value: string) => setDraft({ ...draft, [name]: value });

  async function publish(codes: string[]) {
    await request<void>('/pool/publication' + queryString({ work_quarter: publishQuarter }), {
      method: 'PUT',
      body: JSON.stringify({ codes } satisfies PoolPublicationRequest),
    });
  }

  function publishAll() {
    void publishAction.run(async () => {
      if (!QUARTER_PATTERN.test(publishQuarter)) throw new Error('Квартал обзвона должен быть в формате 2026_Q4');
      const codes = new Set<string>();
      const loadPage = (page: number) => request<ClientListResponse>('/clients' + queryString({
        page, page_size: REGISTRY_PAGE_SIZE,
      }));
      const first = await loadPage(1);
      const pageCount = Math.ceil(first.total / first.page_size);
      first.items.forEach(client => codes.add(client.kcr_code));
      for (let nextPage = 2; nextPage <= pageCount; nextPage++) {
        const result = await loadPage(nextPage);
        if (result.total !== first.total) throw new Error('Реестр изменился. Повторите публикацию.');
        result.items.forEach(client => codes.add(client.kcr_code));
      }
      if (codes.size !== first.total) throw new Error('Реестр изменился. Повторите публикацию.');
      if (!codes.size) throw new Error('В реестре нет клиентов');
      if (!window.confirm(`Опубликовать весь реестр за ${publishQuarter}? Клиентов: ${codes.size}.`)) return;

      const allCodes = [...codes];
      let published = 0;
      try {
        while (published < allCodes.length) {
          const batch = allCodes.slice(published, published + PUBLICATION_BATCH_SIZE);
          await publish(batch);
          published += batch.length;
        }
      } catch (error) {
        if (published) setRevision(current => current + 1);
        const reason = error instanceof Error ? error.message : 'Ошибка запроса';
        throw new Error(`Подтверждена публикация ${published} из ${allCodes.length} клиентов. ${reason}`);
      }
      setSelected({});
      setRevision(current => current + 1);
      publishAction.setNotice(`Опубликовано клиентов: ${published}`);
    });
  }
  return <><h1>Клиенты</h1><form onSubmit={e => { e.preventDefault(); setFilters({ ...draft, search }); setPage(1); }}><label>Код КЦР или ИНН<input className="wide-search" maxLength={256} value={draft.search || ''} onChange={e => change('search', e.target.value)} /></label><div className="filter-line"><label>Квартал сравнения<QuarterInput value={draft.quarter || ''} onChange={value => change('quarter', value)} /></label><label>Динамика<select value={draft.dynamics || ''} onChange={e => change('dynamics', e.target.value)}><option value="">Все</option>{Object.entries(dynamics).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label><label>Длительность<select value={draft.duration || ''} onChange={e => change('duration', e.target.value)}><option value="">Все</option>{Object.entries(durations).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label><label>Признак<select value={draft.tags || ''} onChange={e => change('tags', e.target.value)}><option value="">Все</option>{['VIP', 'NOVICE', 'INTEGRATOR', 'TECH_PARTNER', 'MPP', 'DO_NOT_WORK'].map(tag => <option key={tag}>{tag}</option>)}</select></label></div><div className="filter-line">{[['min_unsubmitted_documents', 'Не передано от'], ['max_unsubmitted_documents', 'Не передано до'], ['min_unsubmitted_percent', 'Процент от'], ['max_unsubmitted_percent', 'Процент до']].map(([key, label]) => <label key={key}>{label}<input className="numeric-filter" type="number" min="0" max={key.includes('percent') ? 100 : 2147483647} step={key.includes('percent') ? 'any' : 1} value={draft[key] || ''} onChange={e => change(key, e.target.value)} /></label>)}</div><button className="button primary">Применить</button> <button type="button" className="button secondary" onClick={() => { setDraft({}); setFilters({}); setPage(1); }}>Сбросить</button></form>
    <Feedback {...result} /><button className="text-link" onClick={() => setRevision(revision + 1)}>Обновить</button>{result.data && <><div className="table-wrap" tabIndex={0} role="region" aria-label="Реестр клиентов"><table><thead><tr>{(user.role === 'ADMIN' ? ['Выбор'] : []).concat(['Код КЦР', 'Название / ИНН', 'За квартал', 'Всего непереданных', 'Динамика', 'Этап', 'Ответственный', 'Признаки']).map(text => <th scope="col" key={text}>{text}</th>)}</tr></thead><tbody>{result.data.items.map(c => <tr key={c.kcr_code} className="client-row" onClick={event => {
      if ((event.target as HTMLElement).closest('button, input, label, a')) return;
      if (window.getSelection()?.toString()) return;
      router.push(`/client/${encodeURIComponent(c.kcr_code)}`);
    }}>{user.role === 'ADMIN' && <td><input type="checkbox" aria-label={`Выбрать ${c.kcr_code}`} checked={!!selected[c.kcr_code]} onChange={e => setSelected({ ...selected, [c.kcr_code]: e.target.checked })} /></td>}<td><button className="text-link" onClick={() => router.push(`/client/${encodeURIComponent(c.kcr_code)}`)}>{c.kcr_code}</button></td><td>{c.organization_name ?? 'Нет названия'}<small className="block">{c.inn ?? 'ИНН не указан'}</small>{c.needs_review && <strong>Требует проверки</strong>}</td><td>{c.quarter_summary?.unsubmitted_documents ?? '—'} / {c.quarter_summary?.total_documents ?? '—'}</td><td>{c.outstanding_documents ?? '—'}</td><td>{c.dynamics ? dynamics[c.dynamics] : 'Не определена'}</td><td>{stages[c.current_stage]}</td><td>{c.assigned_operator_id ?? 'Свободен'}</td><td>{c.tags.join(', ')}</td></tr>)}</tbody></table></div>{!result.data.items.length && <p>Клиенты не найдены</p>}<Pager page={page} total={result.data.total} onChange={setPage} />
      {user.role === 'ADMIN' && <form className="call-form" noValidate onSubmit={e => { e.preventDefault(); void publishAction.run(async () => {
        // Native required bubbles are invisible to the a11y tree (see Imports in management.tsx).
        const codes = result.data!.items.filter(c => selected[c.kcr_code]).map(c => c.kcr_code);
        if (!codes.length) throw new Error('Выберите хотя бы одного клиента');
        if (!QUARTER_PATTERN.test(publishQuarter)) throw new Error('Квартал обзвона должен быть в формате 2026_Q4');
        await publish(codes);
        setSelected({});
        publishAction.setNotice(`Опубликовано клиентов: ${codes.length}`);
      }); }}><h2>Публикация на обзвон</h2><label>Квартал обзвона<QuarterInput required ariaLabel="Квартал обзвона" value={publishQuarter} onChange={setPublishQuarter} /></label><Feedback {...publishAction} /><button className="button primary" disabled={publishAction.busy}>Опубликовать выбранных</button> <button type="button" className="button secondary" disabled={publishAction.busy} onClick={publishAll}>Опубликовать всех</button></form>}</>}</>;
}
export function ClientDetails({ code, user, call }: { code: string; user: User; call: boolean }) {
  const router = useRouter();
  const base = `/clients/${encodeURIComponent(code)}`;
  const [revision, setRevision] = useState(0);
  const [eventPage, setEventPage] = useState(1);
  const [eventQuarter, setEventQuarter] = useState('');
  const [eventQuarterDraft, setEventQuarterDraft] = useState('');
  const [historyPage, setHistoryPage] = useState(1);
  const [quarter, setQuarter] = useState('');
  const [quarterDraft, setQuarterDraft] = useState('');
  const [publishQuarter, setPublishQuarter] = useState('');
  const card = useResource<ClientCard>(base + queryString({ quarter }), revision);
  const events = useResource<EventListResponse>(base + '/events' + queryString({ work_quarter: eventQuarter, page: eventPage, page_size: 20 }), revision);
  const history = useResource<SnapshotHistoryResponse>(base + '/snapshots/history' + queryString({ page: historyPage, page_size: 20, offset: (historyPage - 1) * 20 }), revision);
  const action = useAction();
  const operations = useRef<Record<string, string>>({});
  const takeAttempt = useRef<{ key: string; body: TakeRequest } | null>(null);
  const c = card.data;
  const workQuarter = c?.work_quarter || c?.assignment?.work_quarter;
  const protectedWait = c?.current_stage === 'WAIT_DEADLINE' && !!c.wait_until && c.wait_until > businessDate() && c.assignment?.operator_id !== user.id;
  async function remove(path: string, description: string) {
    if (!window.confirm(description)) return;
    await action.run(async () => {
      const operation_id = operations.current[path] ||= newRequestId();
      await request<void>(path, { method: 'DELETE', body: JSON.stringify({ operation_id }) });
      delete operations.current[path];
      if (path === base) router.push('/'); else { setRevision(revision + 1); action.setNotice('Данные квартала удалены'); }
    });
  }
  return <><button className="back-link" onClick={() => router.push('/')}>← Клиенты</button><Feedback {...card} /><Feedback {...action} /><button className="text-link" onClick={() => setRevision(revision + 1)}>Обновить карточку</button>{c && <><h1>{c.kcr_code} · {c.organization_name ?? 'Нет названия'}</h1><p>ИНН: {c.inn ?? '—'} · КПП: {c.kpp ?? '—'} · {c.status} · {c.tags.join(', ')}</p><p>Менеджер: {c.manager_name ?? '—'}{c.is_blocked && ' · Заблокирован'}</p>{c.contacts && <pre>{JSON.stringify(c.contacts, null, 2)}</pre>}
    <div className="stats-grid">{[['Всего непереданных', c.outstanding_documents ?? '—'], ['Этап', c.current_stage ? stages[c.current_stage] : '—'], ['Ответственный', c.assignment?.operator_name ?? 'Свободен'], ['Ожидание до', c.wait_until ?? '—']].map(([label, value]) => <div className="stat-card" key={label}><small>{label}</small><b>{value}</b></div>)}</div><form onSubmit={e => { e.preventDefault(); setQuarter(quarterDraft); }}><label>Квартал сравнения<QuarterInput value={quarterDraft} onChange={setQuarterDraft} /></label><button className="button secondary">Показать динамику</button></form><p>Динамика: {c.dynamics ? dynamics[c.dynamics] : 'Не определена'} · {c.dynamics_previous_quarter}: {c.dynamics_previous_documents ?? '—'} → {c.dynamics_quarter}: {c.dynamics_current_documents ?? '—'}</p><p>За выбранный квартал: {c.quarter_summary?.unsubmitted_documents ?? '—'} из {c.quarter_summary?.total_documents ?? '—'} ({c.quarter_summary?.unsubmitted_percent == null ? '—' : `${c.quarter_summary.unsubmitted_percent.toFixed(2)}%`})</p>{c.needs_review && <section><h2>Требует проверки</h2><pre>{JSON.stringify(c.inherited_fields, null, 2)}</pre></section>}
    {user.role === 'OPERATOR' && <><button className="button primary" disabled={action.busy || card.loading || protectedWait || !workQuarter} onClick={() => void action.run(async () => {
      const expectedAssignment = c.assignment_id ?? null;
      if (c.assignment && !c.assignment_id) throw new Error('Gateway не вернул ID текущего назначения. Обновите карточку.');
      const key = JSON.stringify([workQuarter, expectedAssignment]);
      if (takeAttempt.current?.key !== key) takeAttempt.current = { key, body: { expected_assignment_id: expectedAssignment, request_id: newRequestId() } };
      const result = await request<TakeResponse | ReassignmentRequest>(base + '/take' + queryString({ work_quarter: workQuarter! }), { method: 'POST', body: JSON.stringify(takeAttempt.current.body) });
      if ('status' in result) {
        action.setNotice(({ PENDING: 'Запрос на перезакрепление ожидает решения до ' + result.expires_at, REJECTED: 'Ответственный отклонил запрос', CANCELLED: 'Запрос отменён из-за изменения состояния', TRANSFERRED: 'Клиент передан вам' })[result.status]);
        if (result.status !== 'PENDING') takeAttempt.current = null;
      } else { takeAttempt.current = null; action.setNotice('Клиент закреплён за вами'); }
      setRevision(revision + 1);
    })}>{c.assignment && c.assignment.operator_id !== user.id ? 'Перезакрепить на себя' : 'Взять в работу'}</button>{protectedWait && <p>Ожидание защищает ответственного до {c.wait_until}.</p>}{!workQuarter && <p>Для клиента пока нет опубликованного цикла обзвона.</p>}</>}
    <EventForm base={base} card={c} user={user} call={call} onSaved={() => { setEventPage(1); setRevision(revision + 1); }} />
    <h2>Срезы по исходным кварталам</h2>{!c.snapshots.length && <p>Срезов нет</p>}{c.snapshots.map(s => <div className="status-row" key={s.quarter}><span>{s.quarter} · {s.unsubmitted_documents} из {s.total_documents} · {s.snapshot_date}</span>{user.role === 'ADMIN' && <button className="button secondary" disabled={action.busy} onClick={() => void remove(`${base}/snapshots/${encodeURIComponent(s.quarter)}`, `Удалить данные квартала ${s.quarter} клиента ${code}? Общая история взаимодействия сохраняется.`)}>Удалить квартал</button>}</div>)}
    <h2>История наблюдений</h2><Feedback {...history} />{history.data && <>{history.data.items.map(s => <div className="status-row" key={s.id}>{s.snapshot_date} · {s.quarter} · Не передано: {s.unsubmitted_documents} · Пакет: {s.import_batch_id}</div>)}{!history.data.items.length && <p>Наблюдений нет</p>}<Pager page={historyPage} total={history.data.total} onChange={setHistoryPage} /></>}
    <h2>Лента событий</h2><form onSubmit={event => { event.preventDefault(); setEventQuarter(eventQuarterDraft); setEventPage(1); }}><label>Квартал событий<QuarterInput ariaLabel="Квартал событий" value={eventQuarterDraft} onChange={setEventQuarterDraft} /></label><button className="button secondary">Показать события</button></form><Feedback {...events} />{events.data && <>{events.data.items.map(event => <div className="status-row" key={event.id}><span>{event.historical_event_date ?? event.created_at} · {event.event_type} · {event.author_id}<br />{event.comment_text}{event.document_quarters?.length ? ` · Документы: ${event.document_quarters.join(', ')}` : ''}{event.call_result && ` · ${event.call_result}`}{event.wait_until && ` · Ожидание до ${event.wait_until}`}</span></div>)}{!events.data.items.length && <p>Событий нет</p>}<Pager page={eventPage} total={events.data.total} onChange={setEventPage} /></>}
    {user.role === 'ADMIN' && <form className="call-form" noValidate onSubmit={e => { e.preventDefault(); void action.run(async () => {
      if (!QUARTER_PATTERN.test(publishQuarter)) throw new Error('Квартал обзвона должен быть в формате 2026_Q4');
      await request<void>(`${base}/publication` + queryString({ work_quarter: publishQuarter }), { method: 'PUT' });
      action.setNotice('Клиент опубликован');
    }); }}><h2>Публикация на обзвон</h2><label>Квартал обзвона<QuarterInput required ariaLabel="Квартал обзвона" value={publishQuarter} onChange={setPublishQuarter} /></label><button className="button primary" disabled={action.busy}>Опубликовать клиента</button></form>}
    {user.role === 'ADMIN' && <button className="button secondary" disabled={action.busy} onClick={() => void remove(base, `Удалить клиента ${code}, его данные и историю работы?`)}>Удалить клиента</button>}
  </>}</>;
}
