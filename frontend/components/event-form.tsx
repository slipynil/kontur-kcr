'use client';

import { useRef, useState } from 'react';
import { ApiError, newRequestId, request } from '@/lib/api';
import type { CallEventCreate, ClientCard, HistoricalCommentCreate, SavedCallEvent, SavedHistoricalComment, User } from '@/lib/contracts';
import { businessDate, Feedback, quarterName, useAction } from './api-ui';
import { DateField } from './date-field';
import { Modal, Tabs } from './presentation';

type EventBody = CallEventCreate | HistoricalCommentCreate;

export const callResults = {
  AGREEMENT: 'Есть договорённость', REFUSAL: 'Отказ', NO_ANSWER: 'Недозвон', WRONG_NUMBER: 'Неверный номер',
};

export function EventForm({ card, user, base, call, onSaved, dialog }: {
  card: ClientCard;
  user: User;
  base: string;
  call: boolean;
  onSaved: (notice: string) => void;
  dialog?: { open: boolean; onClose: () => void; onPendingChange: (pending: boolean) => void };
}) {
  const [kind, setKind] = useState<EventBody['event_type']>(call ? 'CALL_RESULT' : 'HISTORICAL_COMMENT');
  const [assignment, setAssignment] = useState(() => card.assignment?.operator_id === user.id ? card.assignment_id : null);
  const [workQuarter, setWorkQuarter] = useState(card.work_quarter ?? card.assignment?.work_quarter);
  const [result, setResult] = useState<CallEventCreate['call_result']>('AGREEMENT');
  const [comment, setComment] = useState('');
  const [promised, setPromised] = useState('');
  const [wait, setWait] = useState('');
  const [historical, setHistorical] = useState('');
  const [quarters, setQuarters] = useState<string[]>([]);
  const attempt = useRef<EventBody | null>(null);
  const [pending, setPending] = useState(false);
  const action = useAction();

  if (user.role !== 'OPERATOR') return null;
  const isCall = kind === 'CALL_RESULT';

  function markPending(value: boolean) {
    setPending(value);
    dialog?.onPendingChange(value);
  }

  function changeKind(next: string) {
    if (pending || action.busy) return;
    setKind(next as EventBody['event_type']);
    action.reset();
    if (next === 'CALL_RESULT') {
      setAssignment(card.assignment?.operator_id === user.id ? card.assignment_id : null);
      setWorkQuarter(card.work_quarter ?? card.assignment?.work_quarter);
    }
  }

  async function save() {
    if (!attempt.current) {
      const shared = { request_id: newRequestId(), comment_text: comment.trim(), document_quarters: quarters };
      if (isCall) {
        if (!assignment || !workQuarter) throw new Error('Для звонка сначала возьмите клиента в работу и откройте форму заново.');
        if (wait && wait <= businessDate()) throw new Error('Дата ожидания должна быть позже сегодняшней даты по Москве.');
        attempt.current = {
          ...shared, event_type: kind, expected_assignment_id: assignment,
          work_quarter: workQuarter, call_result: result,
          promised_date: promised || null, wait_until: wait || null,
        };
      } else {
        if (!comment.trim()) throw new Error('Введите комментарий.');
        const date = new Date(`${historical}T00:00:00+03:00`);
        if (!Number.isFinite(date.getTime()) || date.getTime() >= Date.now()) throw new Error('Укажите дату комментария в прошлом.');
        attempt.current = {
          ...shared, event_type: kind, historical_event_date: date.toISOString(),
          work_quarter: card.work_quarter ?? null,
        };
      }
    }
    markPending(true);
    const body = attempt.current;
    let event: SavedCallEvent | SavedHistoricalComment;
    try {
      event = await request(base + '/events', { method: 'POST', body: JSON.stringify(body) });
    } catch (error) {
      if (error instanceof ApiError && [400, 403, 404, 409, 422].includes(error.status)) {
        attempt.current = null; markPending(false);
      }
      throw error;
    }
    attempt.current = null; markPending(false);
    setComment(''); setPromised(''); setWait(''); setHistorical(''); setQuarters([]);
    const waitNotApplied = body.event_type === 'CALL_RESULT' && body.wait_until && 'wait_applied' in event && !event.wait_applied;
    const notice = waitNotApplied
      ? 'Звонок сохранён. Срок ожидания не применён, поскольку назначение изменилось.'
      : 'Событие сохранено';
    action.setNotice(notice);
    onSaved(notice);
  }

  const form = <form className="stack event-form" noValidate onSubmit={event => { event.preventDefault(); void action.run(save); }}>
    <Tabs.Root value={kind} onValueChange={changeKind}>
      <Tabs.List className="tab-strip" aria-label="Тип события">
        <Tabs.Trigger className="button" value="CALL_RESULT" disabled={action.busy || pending}>Результат звонка</Tabs.Trigger>
        <Tabs.Trigger className="button" value="HISTORICAL_COMMENT" disabled={action.busy || pending}>Комментарий прошлой даты</Tabs.Trigger>
      </Tabs.List>
      <Tabs.Content value="CALL_RESULT" className="event-fields">
        <fieldset disabled={action.busy || pending}>
          <label>Результат звонка<select value={result} onChange={event => setResult(event.target.value as typeof result)}>
            {Object.entries(callResults).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select></label>
          <div className="form-grid">
            <DateField label="Обещанный срок" value={promised} onChange={setPromised} />
            <DateField label="Ожидание до" value={wait} onChange={setWait} />
          </div>
        </fieldset>
      </Tabs.Content>
      <Tabs.Content value="HISTORICAL_COMMENT" className="event-fields">
        <fieldset disabled={action.busy || pending}>
          <DateField label="Дата комментария" value={historical} onChange={setHistorical} required />
        </fieldset>
      </Tabs.Content>
    </Tabs.Root>
    <fieldset disabled={action.busy || pending}>
      <label>Комментарий<textarea required={!isCall} maxLength={4000} value={comment} onChange={event => setComment(event.target.value)} /></label>
      {card.snapshots.length > 0 && <fieldset className="quarter-checks">
        <legend>Исходные кварталы документов</legend>
        {card.snapshots.map(snapshot => <label className="checkbox-line" key={snapshot.quarter}>
          <input type="checkbox" aria-label={snapshot.quarter} checked={quarters.includes(snapshot.quarter)}
            disabled={quarters.length >= 100 && !quarters.includes(snapshot.quarter)} onChange={event => {
              const checked = event.target.checked;
              setQuarters(current => checked ? [...current, snapshot.quarter] : current.filter(quarter => quarter !== snapshot.quarter));
            }} /> {quarterName(snapshot.quarter)}
        </label>)}
      </fieldset>}
    </fieldset>
    {pending && action.error && <p className="help-text">Повторная отправка сохранит ранее отправленные данные с тем же ID запроса.</p>}
    {isCall && !assignment && <p className="feedback feedback-info">Для записи звонка нужно своё назначение.</p>}
    <Feedback {...action} />
    <div className="modal-actions">
      {dialog && <button type="button" className="button secondary" disabled={action.busy} onClick={dialog.onClose}>Отмена</button>}
      <button className="button primary" disabled={action.busy || (isCall && !assignment)}>Сохранить событие</button>
    </div>
  </form>;
  return dialog ? <Modal open={dialog.open} onClose={dialog.onClose} busy={action.busy}
    title={isCall ? 'Результат звонка' : 'Комментарий прошлой даты'}
    description={`${card.kcr_code} · ${card.organization_name ?? 'Нет названия'}`}>{form}</Modal> : form;
}
