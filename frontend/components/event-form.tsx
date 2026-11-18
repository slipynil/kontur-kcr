'use client';
import { DateField } from './date-field';
import { useRef, useState } from 'react';
import { ApiError, newRequestId, request } from '@/lib/api';
import type { CallEventCreate, ClientCard, HistoricalCommentCreate, SavedCallEvent, SavedHistoricalComment, User } from '@/lib/contracts';
import { businessDate, Feedback, useAction } from './api-ui';

type EventBody = CallEventCreate | HistoricalCommentCreate;

export function EventForm({ card, user, base, call, onSaved }: {
  card: ClientCard;
  user: User;
  base: string;
  call: boolean;
  onSaved: () => void;
}) {
  const [kind, setKind] = useState<'CALL_RESULT' | 'HISTORICAL_COMMENT'>(call ? 'CALL_RESULT' : 'HISTORICAL_COMMENT');
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

  async function save() {
    if (!attempt.current) {
      const shared = { request_id: newRequestId(), comment_text: comment.trim(), document_quarters: quarters };
      if (kind === 'CALL_RESULT') {
        if (!assignment || !workQuarter) throw new Error('Для звонка сначала возьмите клиента в работу и откройте форму заново.');
        if (wait && wait <= businessDate()) throw new Error('Дата ожидания должна быть позже сегодняшней даты по Москве.');
        attempt.current = {
          ...shared, event_type: kind, expected_assignment_id: assignment,
          work_quarter: workQuarter, call_result: result,
          promised_date: promised || null, wait_until: wait || null,
        };
      } else {
        if (!comment.trim()) throw new Error('Введите комментарий.');
        const date = new Date(historical);
        if (!Number.isFinite(date.getTime()) || date.getTime() > Date.now()) throw new Error('Укажите дату комментария в прошлом.');
        attempt.current = {
          ...shared, event_type: kind, historical_event_date: date.toISOString(),
          work_quarter: card.work_quarter ?? null,
        };
      }
    }
    setPending(true);
    const body = attempt.current;
    let event: SavedCallEvent | SavedHistoricalComment;
    try {
      event = await request(base + '/events', { method: 'POST', body: JSON.stringify(body) });
    } catch (error) {
      if (error instanceof ApiError && [400, 403, 404, 409, 422].includes(error.status)) {
        attempt.current = null; setPending(false);
      }
      throw error;
    }
    attempt.current = null; setPending(false);
    setComment(''); setPromised(''); setWait(''); setHistorical(''); setQuarters([]);
    const waitNotApplied = body.event_type === 'CALL_RESULT' && body.wait_until && 'wait_applied' in event && !event.wait_applied;
    action.setNotice(waitNotApplied ? 'Звонок сохранён. Срок ожидания не применён, поскольку назначение изменилось.' : 'Событие сохранено');
    onSaved();
  }

  return <form className="call-form" onSubmit={event => { event.preventDefault(); void action.run(save); }}>
    <h2>Добавить событие</h2>
    <fieldset disabled={action.busy || pending}>
      <label>Тип события<select value={kind} onChange={event => {
        setKind(event.target.value as typeof kind);
        if (event.target.value === 'CALL_RESULT') {
          setAssignment(card.assignment?.operator_id === user.id ? card.assignment_id : null);
          setWorkQuarter(card.work_quarter ?? card.assignment?.work_quarter);
        }
      }}>
        <option value="HISTORICAL_COMMENT">Комментарий прошлой даты</option>
        <option value="CALL_RESULT">Результат звонка</option>
      </select></label>
      {kind === 'CALL_RESULT' && <>
        <label>Результат звонка<select value={result} onChange={event => setResult(event.target.value as typeof result)}>
          {Object.entries({ AGREEMENT: 'Есть договорённость', REFUSAL: 'Отказ', NO_ANSWER: 'Недозвон', WRONG_NUMBER: 'Неверный номер' }).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <DateField label="Обещанный срок" value={promised} onChange={setPromised} />
        <DateField label="Ожидание до" value={wait} onChange={setWait} />
      </>}
      {kind === 'HISTORICAL_COMMENT' && <DateField label="Дата комментария" value={historical} onChange={setHistorical} required withTime />}
      <label>Комментарий<textarea required={kind === 'HISTORICAL_COMMENT'} maxLength={4000} value={comment} onChange={event => setComment(event.target.value)} /></label>
      {card.snapshots.length > 0 && <fieldset><legend>Исходные кварталы документов</legend>
        {card.snapshots.map(snapshot => <label className="checkbox-line" key={snapshot.quarter}>
          <input type="checkbox" checked={quarters.includes(snapshot.quarter)} disabled={quarters.length >= 100 && !quarters.includes(snapshot.quarter)} onChange={event => setQuarters(current => event.target.checked ? [...current, snapshot.quarter] : current.filter(quarter => quarter !== snapshot.quarter))} /> {snapshot.quarter}
        </label>)}
      </fieldset>}
    </fieldset>
    {pending && action.error && <p>Повторная отправка сохранит ранее отправленные данные с тем же ID запроса.</p>}
    {kind === 'CALL_RESULT' && !assignment && <p>Для записи звонка нужно своё назначение. После взятия клиента выберите результат звонка в форме.</p>}
    <Feedback {...action} />
    <button className="button primary" disabled={action.busy || (kind === 'CALL_RESULT' && !assignment)}>Сохранить событие</button>
  </form>;
}
