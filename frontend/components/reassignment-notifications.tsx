'use client';

import { RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { queryString, request } from '@/lib/api';
import type { ReassignmentRequest } from '@/lib/contracts';
import { businessTime, Feedback, useAction, useResource } from './api-ui';
import { Badge, EmptyState, Modal } from './presentation';

const statuses = {
  PENDING: ['Ожидает решения', 'caution'],
  REJECTED: ['Отклонён', 'bad'],
  TRANSFERRED: ['Клиент передан', 'good'],
  CANCELLED: ['Отменён', 'neutral'],
} as const;

export function ReassignmentNotifications({ userId, dialog }: {
  userId: string;
  dialog?: { open: boolean; onClose: () => void };
}) {
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const action = useAction();
  const result = useResource<{ items: ReassignmentRequest[] }>(
    '/reassignment-notifications' + queryString({ limit: 20, offset }), revision, false, 10000,
  );
  const title = 'Запросы на перезакрепление';
  const content = <>
    <Feedback {...result} /><Feedback {...action} />
    <button type="button" className="button text-link" onClick={() => setRevision(current => current + 1)}>
      <RefreshCw aria-hidden="true" />Обновить запросы
    </button>
    {result.data?.items.length === 0 && <EmptyState title="На этой странице нет запросов" />}
    {result.data?.items.length ? <ul className="notification-list">{result.data.items.map(item => {
      const [label, tone] = statuses[item.status];
      return <li className="status-row" key={item.id}>
        <div className="event-main">
          <Badge tone={tone}>{label}</Badge><h3>{item.kcr_code}</h3>
          <div className="meta">
            {item.requester_id === userId ? 'Ваш запрос' : <span className="mono-data">ID заявителя: {item.requester_id}</span>}
            {item.status === 'PENDING' && <span>Решение до {businessTime(item.expires_at)} МСК</span>}
          </div>
        </div>
        {item.status === 'PENDING' && item.owner_id === userId && <button type="button" className="button secondary"
          disabled={action.busy} onClick={() => void action.run(async () => {
            await request<ReassignmentRequest>(`/reassignment-notifications/${encodeURIComponent(item.id)}/reject`, { method: 'POST' });
            setRevision(current => current + 1);
            action.setNotice('Запрос отклонён');
          })}>Отклонить запрос</button>}
      </li>;
    })}</ul> : null}
    <div className="pagination">
      <button type="button" className="button secondary" disabled={offset === 0} onClick={() => setOffset(current => current - 20)}>Предыдущие запросы</button>
      <button type="button" className="button secondary" disabled={!result.data || result.data.items.length < 20} onClick={() => setOffset(current => current + 20)}>Следующие запросы</button>
    </div>
  </>;
  return dialog ? <Modal {...dialog} title={title} description="Ответственный может отклонить входящий запрос до указанного срока." busy={action.busy} wide>
    {content}
    <div className="report-actions"><button type="button" className="button primary" disabled={action.busy} onClick={dialog.onClose}>Готово</button></div>
  </Modal> : <section aria-label={title}><h2>{title}</h2>{content}</section>;
}
