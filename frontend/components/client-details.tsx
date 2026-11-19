'use client';

import { ArrowLeft, Phone, Plus, RefreshCw, Upload } from 'lucide-react';
import { useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { newRequestId, queryString, request } from '@/lib/api';
import type { ClientCard, EventListResponse, ReassignmentRequest, SnapshotHistoryResponse, TakeRequest, TakeResponse, User } from '@/lib/contracts';
import { businessDate, businessTime, displayDate, durations, dynamics, Feedback, Pager, QuarterInput, quarterName, quarterPattern, stages, tags, useAction, useResource } from './api-ui';
import { callResults, EventForm } from './event-form';
import { Badge, ConfirmActions, EmptyState, Metrics, Modal, PageHeader, Tabs } from './presentation';
import { StructuredDetails } from './structured-details';

const eventLabels = {
  CALL_RESULT: 'Результат звонка', HISTORICAL_COMMENT: 'Комментарий прошлой даты',
  EMAIL_SENT: 'Письмо отправлено', STAGE_CHANGE: 'Изменение этапа',
  CLAIM_UPDATE: 'Обновление претензии', OPERATOR_ASSIGN: 'Изменение ответственного',
};

type RemoveTarget = { quarter?: string; href: string };

export function ClientDetails({ code, user, call }: { code: string; user: User; call: boolean }) {
  const router = useRouter();
  const admin = user.role === 'ADMIN';
  const base = `/clients/${encodeURIComponent(code)}`;
  const [section, setSection] = useState('overview');
  const [revision, setRevision] = useState(0);
  const [eventPage, setEventPage] = useState(1);
  const [eventQuarter, setEventQuarter] = useState('');
  const [eventQuarterDraft, setEventQuarterDraft] = useState('');
  const [historyPage, setHistoryPage] = useState(1);
  const [quarter, setQuarter] = useState('');
  const [quarterDraft, setQuarterDraft] = useState('');
  const [publishQuarter, setPublishQuarter] = useState('');
  const [publishOpen, setPublishOpen] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<RemoveTarget | null>(null);
  const [eventOpen, setEventOpen] = useState(call && !admin);
  const [eventCall, setEventCall] = useState(call);
  const [eventKey, setEventKey] = useState(0);
  const [pendingEventCard, setPendingEventCard] = useState<ClientCard | null>(null);
  const card = useResource<ClientCard>(base + queryString({ quarter }), revision);
  const events = useResource<EventListResponse>(section === 'events' ?
    base + '/events' + queryString({ work_quarter: eventQuarter, page: eventPage, page_size: 20 }) : null, revision);
  const history = useResource<SnapshotHistoryResponse>(section === 'history' ?
    base + '/snapshots/history' + queryString({ page: historyPage, page_size: 20, offset: (historyPage - 1) * 20 }) : null, revision);
  const action = useAction();
  const operations = useRef<Record<string, string>>({});
  const takeAttempt = useRef<{ key: string; body: TakeRequest } | null>(null);
  const c = card.data;
  const eventCard = pendingEventCard ?? c;
  const workQuarter = c?.work_quarter || c?.assignment?.work_quarter;
  const own = c?.assignment?.operator_id === user.id;
  const protectedWait = c?.current_stage === 'WAIT_DEADLINE' && !!c.wait_until && c.wait_until > businessDate() && !own;

  function refresh() { setRevision(value => value + 1); }

  function openEvent(asCall: boolean) {
    action.reset();
    if (!pendingEventCard) {
      setEventCall(asCall);
      setEventKey(key => key + 1);
    }
    setEventOpen(true);
  }

  function remove() {
    if (!removeTarget) return;
    const { href, quarter: removedQuarter } = removeTarget;
    void action.run(async () => {
      const operation_id = operations.current[href] ||= newRequestId();
      await request<void>(href, { method: 'DELETE', body: JSON.stringify({ operation_id }) });
      delete operations.current[href];
      setRemoveTarget(null);
      if (removedQuarter) { refresh(); action.setNotice('Данные квартала удалены'); }
      else router.push('/');
    });
  }

  function take() {
    if (!c || !workQuarter || protectedWait) return;
    void action.run(async () => {
      const expectedAssignment = c.assignment_id ?? null;
      if (c.assignment && !c.assignment_id) throw new Error('Не удалось определить текущее назначение. Обновите карточку.');
      const key = JSON.stringify([workQuarter, expectedAssignment]);
      if (takeAttempt.current?.key !== key) takeAttempt.current = { key, body: { expected_assignment_id: expectedAssignment, request_id: newRequestId() } };
      const result = await request<TakeResponse | ReassignmentRequest>(base + '/take' + queryString({ work_quarter: workQuarter }), {
        method: 'POST', body: JSON.stringify(takeAttempt.current.body),
      });
      if ('status' in result) {
        action.setNotice({
          PENDING: `Запрос на перезакрепление ожидает решения до ${businessTime(result.expires_at)} МСК`,
          REJECTED: 'Ответственный отклонил запрос', CANCELLED: 'Запрос отменён из-за изменения состояния',
          TRANSFERRED: 'Клиент передан вам',
        }[result.status]);
        if (result.status !== 'PENDING') takeAttempt.current = null;
      } else {
        takeAttempt.current = null;
        action.setNotice('Клиент закреплён за вами');
      }
      refresh();
    });
  }

  return <>
    <PageHeader title={c?.organization_name ? `${c.organization_name} · ${code}` : code} description="Карточка клиента" />
    <div className="card-top">
      <button type="button" className="button text-link" onClick={() => router.push('/')}><ArrowLeft aria-hidden="true" />К реестру</button>
      <button type="button" className="button text-link" onClick={refresh}><RefreshCw aria-hidden="true" />Обновить карточку</button>
    </div>
    <Feedback {...card} />
    {!removeTarget && !publishOpen && <Feedback {...action} />}
    {c && <>
      <Metrics items={[
        ['Всего непереданных', c.outstanding_documents],
        ['Этап', c.current_stage ? stages[c.current_stage] : '—'],
        ['Ответственный', c.assignment ? c.assignment.operator_name || 'Имя не указано' : workQuarter ? 'Свободен' : '—'],
        ['Ожидание до', displayDate(c.wait_until)],
      ]} />
      <Tabs.Root value={section} onValueChange={setSection}>
        <div className="card-tabs-bar">
          <Tabs.List className="tab-strip" aria-label="Разделы карточки">
            {[['overview', 'Обзор'], ['documents', 'Документы'], ['history', 'История наблюдений'], ['events', 'Лента событий']].map(([value, label]) =>
              <Tabs.Trigger className="button" key={value} value={value}>{label}</Tabs.Trigger>,
            )}
          </Tabs.List>
          <div className="inline-actions">
            {admin ? <>
              <button className="button primary" type="button" aria-haspopup="dialog" onClick={() => { action.reset(); setPublishOpen(true); }}><Upload aria-hidden="true" />На обзвон</button>
              <button className="button secondary" type="button" aria-haspopup="dialog" onClick={() => { action.reset(); setRemoveTarget({ href: base }); }}>Удалить клиента</button>
            </> : <>
              <button className="button secondary" type="button" aria-haspopup="dialog" onClick={() => openEvent(false)}><Plus aria-hidden="true" />Добавить событие</button>
              {own ? <button className="button primary" type="button" aria-haspopup="dialog" onClick={() => openEvent(true)} disabled={!c.assignment_id || !workQuarter}><Phone aria-hidden="true" />Записать звонок</button>
                : <button className="button primary" type="button" onClick={take} disabled={action.busy || card.loading || protectedWait || !workQuarter}>
                  {c.assignment ? 'Перезакрепить на себя' : 'Взять в работу'}
                </button>}
            </>}
          </div>
        </div>
        {!admin && protectedWait && <p className="feedback feedback-caution">Ожидание защищает ответственного до {displayDate(c.wait_until)}.</p>}
        {!admin && !workQuarter && <p className="feedback feedback-info">Для клиента пока нет опубликованного цикла обзвона.</p>}
        <Tabs.Content className="card-section" value="overview">
          <div className="card-overview">
            <section>
              <h2>Сведения о клиенте</h2>
              <dl className="data-grid">
                <div><dt>ИНН</dt><dd>{c.inn ?? '—'}</dd></div>
                <div><dt>КПП</dt><dd>{c.kpp ?? '—'}</dd></div>
                <div><dt>Блокировка</dt><dd><Badge tone={c.is_blocked ? 'bad' : 'good'}>
                  {c.is_blocked ? 'Заблокирован' : 'Нет блокировки'}
                </Badge></dd></div>
                <div><dt>Менеджер</dt><dd>{c.manager_name ?? '—'}</dd></div>
                <div><dt>Признаки</dt><dd>{c.tags.map(tag => tags[tag] ?? tag).join(', ') || '—'}</dd></div>
                {c.duration && <div><dt>Длительность</dt><dd><Badge tone={c.duration === 'FIRST_TIME' ? 'caution' : 'bad'}>{durations[c.duration]}</Badge></dd></div>}
              </dl>
              <div className="contact-block"><h2>Контакты</h2><StructuredDetails values={c.contacts} /></div>
              {c.needs_review && <div className="review-block">
                <h2>Требует проверки</h2><StructuredDetails values={c.inherited_fields} />
              </div>}
            </section>
            <section className="dynamics-panel">
              <h2>Динамика документов</h2>
              <form className="inline-form" onSubmit={event => { event.preventDefault(); setQuarter(quarterDraft); }}>
                <label>Квартал сравнения<QuarterInput value={quarterDraft} onChange={setQuarterDraft} /></label>
                <button className="button secondary">Показать динамику</button>
              </form>
              <div className="comparison-line">
                <span>{quarterName(c.dynamics_previous_quarter)} <strong>{c.dynamics_previous_documents ?? '—'}</strong></span>
                <span aria-hidden="true">→</span>
                <span>{quarterName(c.dynamics_quarter)} <strong>{c.dynamics_current_documents ?? '—'}</strong></span>
                <Badge tone={c.dynamics === 'GROWING' ? 'bad' : c.dynamics === 'REDUCING' || c.dynamics === 'ALL_SUBMITTED' ? 'good' : 'neutral'}>
                  {c.dynamics ? dynamics[c.dynamics] : 'Не определена'}
                </Badge>
              </div>
              <p className="summary-text">За выбранный квартал: {c.quarter_summary?.unsubmitted_documents ?? '—'} из {c.quarter_summary?.total_documents ?? '—'}
                {c.quarter_summary?.unsubmitted_percent != null && ` (${c.quarter_summary.unsubmitted_percent.toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%)`}
              </p>
            </section>
          </div>
        </Tabs.Content>
        <Tabs.Content className="card-section" value="documents">
          <h2>Срезы по исходным кварталам</h2>
          <p className="help-text">Актуальный остаток по каждому исходному периоду.</p>
          {!c.snapshots.length ? <EmptyState title="Срезов нет" /> : <div className="table-wrap subtable" role="region" aria-label="Срезы" tabIndex={0}>
            <table><thead><tr>{['Квартал', 'Дата среза', 'Всего документов', 'Не передано', 'Не передано, %', ...(admin ? ['Действие'] : [])].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
              <tbody>{c.snapshots.map(snapshot => <tr key={snapshot.quarter}>
                <td>{quarterName(snapshot.quarter)}</td><td>{displayDate(snapshot.snapshot_date)}</td><td>{snapshot.total_documents}</td>
                <td>{snapshot.unsubmitted_documents}</td><td>{snapshot.total_documents ?
                  `${(snapshot.unsubmitted_documents / snapshot.total_documents * 100).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}%` : '—'}</td>
                {admin && <td><button className="button secondary" type="button" aria-haspopup="dialog" onClick={() => {
                  action.reset(); setRemoveTarget({ href: `${base}/snapshots/${encodeURIComponent(snapshot.quarter)}`, quarter: snapshot.quarter });
                }}>Удалить квартал</button></td>}
              </tr>)}</tbody>
            </table>
          </div>}
        </Tabs.Content>
        <Tabs.Content className="card-section" value="history">
          <h2>История наблюдений</h2><p className="help-text">Изменения числа непереданных документов по датам среза.</p>
          <Feedback {...history} />
          {history.data && (history.data.items.length ? <>
            <div className="table-wrap subtable" role="region" aria-label="Наблюдения" tabIndex={0}>
              <table><thead><tr>{['Дата среза', 'Квартал', 'Не передано', 'Пакет импорта'].map(label => <th key={label} scope="col">{label}</th>)}</tr></thead>
                <tbody>{history.data.items.map(item => <tr key={item.id}><td>{displayDate(item.snapshot_date)}</td><td>{quarterName(item.quarter)}</td>
                  <td>{item.unsubmitted_documents}</td><td className="mono-data">{item.import_batch_id}</td></tr>)}</tbody>
              </table>
            </div>
            <Pager page={historyPage} total={history.data.total} pageSize={history.data.page_size} onChange={setHistoryPage} />
          </> : <EmptyState title="Наблюдений нет" />)}
        </Tabs.Content>
        <Tabs.Content className="card-section" value="events">
          <div className="section-header event-header">
            <h2>Лента событий</h2>
            <form className="inline-form" onSubmit={event => { event.preventDefault(); setEventQuarter(eventQuarterDraft); setEventPage(1); }}>
              <label>Квартал событий<QuarterInput ariaLabel="Квартал событий" value={eventQuarterDraft} onChange={setEventQuarterDraft} /></label>
              <button className="button secondary">Показать события</button>
            </form>
          </div>
          <Feedback {...events} />
          {events.data && (events.data.items.length ? <>
            <ul className="event-list">{events.data.items.map(event => {
              const date = event.historical_event_date ?? event.created_at;
              const knownAuthor = event.author_id === user.id ? user.login
                : event.author_id === c.assignment?.operator_id ? c.assignment.operator_name : null;
              return <li key={event.id}><div className="event-main">
                <h3>{eventLabels[event.event_type] ?? event.event_type}</h3>
                {event.comment_text && <p>{event.comment_text}</p>}
                <div className="meta">
                  {event.call_result && <span>{callResults[event.call_result]}</span>}
                  {event.document_quarters?.length ? <span>Документы: {event.document_quarters.map(quarterName).join(', ')}</span> : null}
                  {event.promised_date && <span>Обещанный срок: {displayDate(event.promised_date)}</span>}
                  {event.wait_until && <span>Ожидание до: {displayDate(event.wait_until)}</span>}
                </div>
              </div><div><time dateTime={date}>{businessTime(date)} МСК</time><div className="meta mono-data">{knownAuthor || (event.author_id ? `ID автора: ${event.author_id}` : '—')}</div></div></li>;
            })}</ul>
            <Pager page={eventPage} total={events.data.total} pageSize={events.data.page_size} onChange={setEventPage} />
          </> : <EmptyState title="Событий нет" />)}
        </Tabs.Content>
      </Tabs.Root>
      {admin && <Modal open={publishOpen} title="Публикация на обзвон" description={`${code} · ${c.organization_name ?? 'Нет названия'}`}
        busy={action.busy} onClose={() => { setPublishOpen(false); action.reset(); }}>
        <form className="stack" noValidate onSubmit={event => {
          event.preventDefault();
          void action.run(async () => {
            if (!quarterPattern.test(publishQuarter)) throw new Error('Квартал обзвона должен быть в формате 2026_Q4');
            await request<void>(base + '/publication' + queryString({ work_quarter: publishQuarter }), { method: 'PUT' });
            setPublishOpen(false); action.setNotice('Клиент опубликован'); refresh();
          });
        }}>
          <label>Квартал обзвона<QuarterInput required ariaLabel="Квартал обзвона" value={publishQuarter} onChange={setPublishQuarter} /></label>
          <Feedback error={action.error} />
          <div className="modal-actions">
            <button type="button" className="button secondary" disabled={action.busy} onClick={() => { setPublishOpen(false); action.reset(); }}>Отмена</button>
            <button className="button primary" disabled={action.busy}>Опубликовать клиента</button>
          </div>
        </form>
      </Modal>}
      {admin && <Modal open={!!removeTarget} title={removeTarget?.quarter ? 'Удалить данные квартала?' : 'Удалить клиента?'}
        description={removeTarget?.quarter ? 'Остальные кварталы и общая история взаимодействия сохранятся.' : 'Удалятся данные клиента и связанная история работы.'}
        busy={action.busy} onClose={() => { setRemoveTarget(null); action.reset(); }}>
        <dl className="dialog-details">
          <dt>Клиент</dt><dd>{c.organization_name ?? code}</dd><dt>Код КЦР</dt><dd>{code}</dd>
          {removeTarget?.quarter && <><dt>Квартал</dt><dd>{quarterName(removeTarget.quarter)}</dd></>}
        </dl>
        <Feedback error={action.error} />
        <ConfirmActions label={removeTarget?.quarter ? 'Удалить квартал' : 'Удалить клиента'} danger busy={action.busy}
          onConfirm={remove} onClose={() => { setRemoveTarget(null); action.reset(); }} />
      </Modal>}
    </>}
      {!admin && eventCard && <EventForm key={eventKey} base={base} card={eventCard} user={user} call={eventCall}
        dialog={{ open: eventOpen, onClose: () => setEventOpen(false), onPendingChange: value => setPendingEventCard(value ? eventCard : null) }}
        onSaved={notice => {
          setEventOpen(false); setSection('events'); setEventPage(1);
          setEventQuarter(''); setEventQuarterDraft('');
          action.setNotice(notice); refresh();
        }} />}
  </>;
}
