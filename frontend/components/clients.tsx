'use client';
import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { queryString, request } from '@/lib/api';
import type { ClientCard, ClientListResponse, ClientEvent, ClientEventCreate, EventListResponse, SnapshotHistoryResponse, TakeResponse, User, Stage } from '@/lib/contracts';
import { businessDate, dynamics, durations, Feedback, Pager, QuarterInput, stages, useAction, useResource } from './api-ui';

export function Clients() {
  const router = useRouter();
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const result = useResource<ClientListResponse>('/clients' + queryString({ ...filters, page, page_size: 20 }), revision);
  const change = (name: string, value: string) => setDraft({ ...draft, [name]: value });
  return <><h1>Клиенты</h1><form onSubmit={e => { e.preventDefault(); setFilters(draft); setPage(1); }}><label>Код КЦР или ИНН<input className="wide-search" maxLength={256} value={draft.search || ''} onChange={e => change('search', e.target.value)} /></label><div className="filter-line"><label>Квартал сравнения<QuarterInput value={draft.quarter || ''} onChange={value => change('quarter', value)} /></label><label>Динамика<select value={draft.dynamics || ''} onChange={e => change('dynamics', e.target.value)}><option value="">Все</option>{Object.entries(dynamics).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label><label>Длительность<select value={draft.duration || ''} onChange={e => change('duration', e.target.value)}><option value="">Все</option>{Object.entries(durations).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label><label>Признак<select value={draft.tags || ''} onChange={e => change('tags', e.target.value)}><option value="">Все</option>{['VIP', 'NOVICE', 'INTEGRATOR', 'TECH_PARTNER', 'MPP', 'DO_NOT_WORK'].map(tag => <option key={tag}>{tag}</option>)}</select></label></div><div className="filter-line">{[['min_unsubmitted_documents', 'Не передано от'], ['max_unsubmitted_documents', 'Не передано до'], ['min_unsubmitted_percent', 'Процент от'], ['max_unsubmitted_percent', 'Процент до']].map(([key, label]) => <label key={key}>{label}<input type="number" min="0" max={key.includes('percent') ? 100 : 2147483647} step={key.includes('percent') ? 'any' : 1} value={draft[key] || ''} onChange={e => change(key, e.target.value)} /></label>)}</div><button className="button primary">Применить</button> <button type="button" className="button secondary" onClick={() => { setDraft({}); setFilters({}); setPage(1); }}>Сбросить</button></form>
    <Feedback {...result} /><button className="text-link" onClick={() => setRevision(revision + 1)}>Обновить</button>{result.data && <><div className="table-wrap"><table><thead><tr>{['Код КЦР', 'Название / ИНН', 'За квартал', 'Всего непереданных', 'Динамика', 'Этап', 'Ответственный', 'Признаки'].map(text => <th key={text}>{text}</th>)}</tr></thead><tbody>{result.data.items.map(c => <tr key={c.kcr_code}><td><button className="text-link" onClick={() => router.push(`/client/${encodeURIComponent(c.kcr_code)}`)}>{c.kcr_code}</button></td><td>{c.organization_name ?? 'Нет названия'}<small className="block">{c.inn ?? 'ИНН не указан'}</small>{c.needs_review && <strong>Требует проверки</strong>}</td><td>{c.quarter_summary?.unsubmitted_documents ?? '—'} / {c.quarter_summary?.total_documents ?? '—'}</td><td>{c.outstanding_documents ?? '—'}</td><td>{c.dynamics ? dynamics[c.dynamics] : 'Не определена'}</td><td>{stages[c.current_stage]}</td><td>{c.assigned_operator_id ?? 'Свободен'}</td><td>{c.tags.join(', ')}</td></tr>)}</tbody></table></div>{!result.data.items.length && <p>Клиенты не найдены</p>}<Pager page={page} total={result.data.total} onChange={setPage} /></>}</>;
}
export function ClientDetails({ code, user, call }: { code: string; user: User; call: boolean }) {
  const router = useRouter();
  const base = `/clients/${encodeURIComponent(code)}`;
  const [revision, setRevision] = useState(0);
  const [eventPage, setEventPage] = useState(1);
  const [historyPage, setHistoryPage] = useState(1);
  const [quarter, setQuarter] = useState('');
  const [quarterDraft, setQuarterDraft] = useState('');
  const card = useResource<ClientCard>(base + queryString({ quarter }), revision);
  const events = useResource<EventListResponse>(base + '/events' + queryString({ page: eventPage, page_size: 20 }), revision);
  const history = useResource<SnapshotHistoryResponse>(base + '/snapshots/history' + queryString({ page: historyPage, page_size: 20, offset: (historyPage - 1) * 20 }), revision);
  const action = useAction();
  const operations = useRef<Record<string, string>>({});
  const c = card.data;
  const workQuarter = c?.work_quarter || c?.assignment?.work_quarter;
  const protectedWait = c?.current_stage === 'WAIT_DEADLINE' && !!c.wait_until && c.wait_until > businessDate() && c.assignment?.operator_id !== user.id;
  async function remove(path: string, description: string) {
    if (!window.confirm(description)) return;
    await action.run(async () => {
      const operation_id = operations.current[path] ||= crypto.randomUUID();
      await request<void>(path, { method: 'DELETE', body: JSON.stringify({ operation_id }) });
      delete operations.current[path];
      if (path === base) router.push('/'); else { setRevision(revision + 1); action.setNotice('Данные квартала удалены'); }
    });
  }
  return <><button className="back-link" onClick={() => router.push('/')}>← Клиенты</button><Feedback {...card} /><Feedback {...action} /><button className="text-link" onClick={() => setRevision(revision + 1)}>Обновить карточку</button>{c && <><h1>{c.kcr_code} · {c.organization_name ?? 'Нет названия'}</h1><p>ИНН: {c.inn ?? '—'} · КПП: {c.kpp ?? '—'} · {c.status} · {c.tags.join(', ')}</p><p>Менеджер: {c.manager_name ?? '—'}{c.is_blocked && ' · Заблокирован'}</p>{c.contacts && <pre>{JSON.stringify(c.contacts, null, 2)}</pre>}
    <div className="stats-grid">{[['Всего непереданных', c.outstanding_documents ?? '—'], ['Этап', stages[c.current_stage]], ['Ответственный', c.assignment?.operator_name ?? 'Свободен'], ['Ожидание до', c.wait_until ?? '—']].map(([label, value]) => <div className="stat-card" key={label}><small>{label}</small><b>{value}</b></div>)}</div><form onSubmit={e => { e.preventDefault(); setQuarter(quarterDraft); }}><label>Квартал сравнения<QuarterInput value={quarterDraft} onChange={setQuarterDraft} /></label><button className="button secondary">Показать динамику</button></form><p>Динамика: {c.dynamics ? dynamics[c.dynamics] : 'Не определена'} · {c.dynamics_previous_quarter}: {c.dynamics_previous_documents ?? '—'} → {c.dynamics_quarter}: {c.dynamics_current_documents ?? '—'}</p><p>За выбранный квартал: {c.quarter_summary?.unsubmitted_documents ?? '—'} из {c.quarter_summary?.total_documents ?? '—'} ({c.quarter_summary?.unsubmitted_percent == null ? '—' : `${c.quarter_summary.unsubmitted_percent.toFixed(2)}%`})</p>{c.needs_review && <section><h2>Требует проверки</h2><pre>{JSON.stringify(c.inherited_fields, null, 2)}</pre></section>}
    {user.role === 'OPERATOR' && <><button className="button primary" disabled={action.busy || protectedWait || !workQuarter} onClick={() => void action.run(async () => {
      await request<TakeResponse>(base + '/take' + queryString({ work_quarter: workQuarter! }), { method: 'POST' });
      setRevision(revision + 1); action.setNotice('Клиент закреплён за вами');
    })}>{c.assignment && c.assignment.operator_id !== user.id ? 'Перезакрепить на себя' : 'Взять в работу'}</button>{protectedWait && <p>Ожидание защищает ответственного до {c.wait_until}.</p>}{!workQuarter && <p>Для клиента пока нет цикла обзвона.</p>}</>}
    <EventForm key={`${code}:${workQuarter}`} base={base} workQuarter={workQuarter} call={call} onSaved={() => { setEventPage(1); setRevision(revision + 1); }} />
    <h2>Срезы по исходным кварталам</h2>{!c.snapshots.length && <p>Срезов нет</p>}{c.snapshots.map(s => <div className="status-row" key={s.quarter}><span>{s.quarter} · {s.unsubmitted_documents} из {s.total_documents} · {s.snapshot_date}</span>{user.role === 'ADMIN' && <button className="button secondary" disabled={action.busy} onClick={() => void remove(`${base}/snapshots/${encodeURIComponent(s.quarter)}`, `Удалить данные квартала ${s.quarter} клиента ${code}? Общая история взаимодействия сохраняется.`)}>Удалить квартал</button>}</div>)}
    <h2>История наблюдений</h2><Feedback {...history} />{history.data && <>{history.data.items.map(s => <div className="status-row" key={s.id}>{s.snapshot_date} · {s.quarter} · Не передано: {s.unsubmitted_documents} · Пакет: {s.import_batch_id}</div>)}{!history.data.items.length && <p>Наблюдений нет</p>}<Pager page={historyPage} total={history.data.total} onChange={setHistoryPage} /></>}
    <h2>Лента событий</h2><Feedback {...events} />{events.data && <>{events.data.items.map(event => <div className="status-row" key={event.id}><span>{event.historical_event_date ?? event.created_at} · {event.event_type} · {event.author_id}<br />{event.comment_text}{event.call_result && ` · ${event.call_result}`}{event.wait_until && ` · Ожидание до ${event.wait_until}`}</span></div>)}{!events.data.items.length && <p>Событий нет</p>}<Pager page={eventPage} total={events.data.total} onChange={setEventPage} /></>}
    {user.role === 'ADMIN' && <button className="button secondary" disabled={action.busy} onClick={() => void remove(base, `Удалить клиента ${code}, его данные и историю работы?`)}>Удалить клиента</button>}
  </>}</>;
}
function EventForm({ base, workQuarter, call, onSaved }: { base: string; workQuarter?: string; call: boolean; onSaved: () => void }) {
  const [kind, setKind] = useState(call ? 'CALL_RESULT' : 'HISTORICAL_COMMENT');
  const [result, setResult] = useState<ClientEventCreate['call_result']>('AGREEMENT');
  const [comment, setComment] = useState('');
  const [date, setDate] = useState('');
  const [historical, setHistorical] = useState('');
  const [stage, setStage] = useState<Stage>('IN_WORK');
  const action = useAction();
  return <form className="call-form" onSubmit={e => { e.preventDefault(); void action.run(async () => {
    if (kind !== 'HISTORICAL_COMMENT' && !workQuarter) throw new Error('Нет текущего цикла обзвона');
    if (kind === 'HISTORICAL_COMMENT' && historical && new Date(historical).getTime() > Date.now()) throw new Error('Дата комментария должна быть в прошлом');
    const body: ClientEventCreate = { event_type: kind as ClientEventCreate['event_type'], comment_text: comment, work_quarter: workQuarter ?? null };
    if (kind === 'CALL_RESULT') { body.call_result = result; body.promised_date = date || null; }
    if (kind === 'STAGE_CHANGE') { body.stage = stage; if (stage === 'WAIT_DEADLINE') body.wait_until = date; }
    if (kind === 'HISTORICAL_COMMENT' && historical) body.historical_event_date = new Date(historical).toISOString();
    await request<ClientEvent>(base + '/events', { method: 'POST', body: JSON.stringify(body) });
    setComment(''); setDate(''); setHistorical(''); action.setNotice('Событие сохранено'); onSaved();
  }); }}><h2>Добавить событие</h2><label>Тип события<select value={kind} onChange={e => { setKind(e.target.value); setDate(''); }}><option value="HISTORICAL_COMMENT">Комментарий прошлой даты</option><option value="CALL_RESULT">Результат звонка</option><option value="STAGE_CHANGE">Изменение этапа</option></select></label>{kind === 'CALL_RESULT' && <><label>Результат звонка<select value={result} onChange={e => setResult(e.target.value as ClientEventCreate['call_result'])}>{Object.entries({ AGREEMENT: 'Есть договорённость', REFUSAL: 'Отказ', NO_ANSWER: 'Недозвон', WRONG_NUMBER: 'Неверный номер' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>Обещанный срок<input type="date" value={date} onChange={e => setDate(e.target.value)} /></label></>}{kind === 'STAGE_CHANGE' && <><label>Новый этап<select value={stage} onChange={e => setStage(e.target.value as Stage)}>{Object.entries(stages).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{stage === 'WAIT_DEADLINE' && <label>Ожидание до<input required type="date" value={date} onChange={e => setDate(e.target.value)} /></label>}</>}{kind === 'HISTORICAL_COMMENT' && <label>Дата комментария<input required type="datetime-local" value={historical} onChange={e => setHistorical(e.target.value)} /></label>}<label>Комментарий<textarea required value={comment} onChange={e => setComment(e.target.value)} /></label><Feedback {...action} /><button className="button primary" disabled={action.busy || (kind !== 'HISTORICAL_COMMENT' && !workQuarter)}>Сохранить событие</button></form>;
}
