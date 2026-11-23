import type { ClientAssignmentInfo, EventListResponse, User } from '@/lib/contracts';
import { businessTime, displayDate, quarterName, stages } from './api-ui';
import { callResults } from './event-form';

type Event = EventListResponse['items'][number];

const eventLabels = {
  CALL_RESULT: 'Результат звонка',
  HISTORICAL_COMMENT: 'Комментарий прошлой даты',
  COMMENT: 'Комментарий',
  PUBLICATION: 'Передан в обзвон',
  EMAIL_SENT: 'Письмо отправлено',
  STAGE_CHANGE: 'Изменение этапа',
  CLAIM_UPDATE: 'Обновление претензии',
  OPERATOR_ASSIGN: 'Изменение ответственного',
};

const assignmentLabels = {
  TAKE: 'Взят в работу',
  REASSIGN: 'Перезакреплён',
  INHERITED: 'Ответственный перенесён в новый цикл',
};

const stageReasons = {
  TAKE: 'Клиент взят в работу',
  SNAPSHOT: 'Цикл создан по срезу',
  WAIT_SET: 'Установлен срок ожидания',
  WAIT_EXPIRED: 'Срок ожидания истёк',
  RESTORE: 'Состояние восстановлено после отката',
};

export function ClientHistory({ events, user, assignment }: {
  events: Event[];
  user: User;
  assignment: ClientAssignmentInfo | null;
}) {
  const groups: { cycle: string | null; quarter: string | null; items: Event[] }[] = [];
  // Group adjacent events without changing the order returned by the API.
  for (const event of events) {
    const quarter = event.work_quarter ?? null;
    const cycle = 'cycle_id' in event ? event.cycle_id ?? quarter : quarter;
    const last = groups.at(-1);
    if (last && last.cycle === cycle) last.items.push(event);
    else groups.push({ cycle, quarter, items: [event] });
  }

  function operatorName(id: string) {
    if (id === user.id) return user.login;
    return id === assignment?.operator_id ? assignment.operator_name : null;
  }

  return groups.map(({ quarter, items }) => (
    <section className="event-cycle" key={items[0].id}>
      <h3>{quarter ? quarterName(quarter) : 'Вне цикла обзвона'}</h3>
      <ol className="event-list" role="list">
        {items.map(event => {
          const history = 'occurred_at' in event ? event : null;
          const payload = history?.payload;
          const date = history?.occurred_at ?? event.historical_event_date ?? event.created_at;
          const actor = history ? history.actor_id : event.author_id;
          const author = event.actor_name || (actor ? operatorName(actor) : null);
          const comment = payload?.comment_text ?? (history ? null : event.comment_text);
          const result = payload?.call_result ?? event.call_result;
          const promised = payload?.promised_date ?? event.promised_date;
          const wait = payload?.wait_until ?? event.wait_until;
          const operator = payload?.operator_id;
          const previous = payload?.previous_operator_id;
          const fromStage = payload?.from_stage;
          const toStage = payload?.to_stage;
          const title = payload?.kind ? assignmentLabels[payload.kind] : eventLabels[event.event_type] ?? event.event_type;

          return <li key={event.id}>
            <div className="event-main">
              <h4>{title}</h4>
              {comment && <p>{comment}</p>}
              <div className="meta">
                {result && <span>{callResults[result]}</span>}
                {event.document_quarters?.length ? <span>Документы: {event.document_quarters.map(quarterName).join(', ')}</span> : null}
                {promised && <span>Обещанный срок: {displayDate(promised)}</span>}
                {wait && <span>Ожидание до: {displayDate(wait)}</span>}
                {previous && <span>Прежний ответственный: {operatorName(previous) || previous}</span>}
                {operator && <span>Новый ответственный: {operatorName(operator) || operator}</span>}
                {toStage && <span>Этап: {fromStage ? `${stages[fromStage]} → ` : ''}{stages[toStage]}</span>}
                {payload?.reason && <span>{stageReasons[payload.reason]}</span>}
              </div>
            </div>
            <div>
              <time dateTime={date}>{businessTime(date)} МСК</time>
              {author && <div className="meta">{author}</div>}
            </div>
          </li>;
        })}
      </ol>
    </section>
  ));
}
