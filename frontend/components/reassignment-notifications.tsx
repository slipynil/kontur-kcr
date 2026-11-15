'use client';
import { useState } from 'react';
import { queryString, request } from '@/lib/api';
import type { ReassignmentRequest } from '@/lib/contracts';
import { Feedback, useAction, useResource } from './api-ui';

const statusLabels = { PENDING: 'Ожидает решения', REJECTED: 'Отклонён', TRANSFERRED: 'Клиент передан', CANCELLED: 'Отменён' };

export function ReassignmentNotifications({ userId }: { userId: string }) {
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const action = useAction();
  const result = useResource<{ items: ReassignmentRequest[] }>(
    '/reassignment-notifications' + queryString({ limit: 20, offset }), revision, false, 10000,
  );
  return <section aria-label="Запросы на перезакрепление">
    <h2>Запросы на перезакрепление</h2>
    <Feedback {...result} /><Feedback {...action} />
    <button className="text-link" onClick={() => setRevision(current => current + 1)}>Обновить запросы</button>
    {result.data?.items.map(item => <div className="status-row" key={item.id}>
      <span>{item.kcr_code} · {statusLabels[item.status]} · {item.requester_id}
        {item.status === 'PENDING' && ` · Решение до ${new Date(item.expires_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })} МСК`}
      </span>
      {item.status === 'PENDING' && item.owner_id === userId && <button className="button secondary" disabled={action.busy} onClick={() => void action.run(async () => {
        await request<ReassignmentRequest>(`/reassignment-notifications/${encodeURIComponent(item.id)}/reject`, { method: 'POST' });
        setRevision(current => current + 1);
        action.setNotice('Запрос отклонён');
      })}>Отклонить запрос</button>}
    </div>)}
    <div className="pagination">
      <button className="button secondary" disabled={offset === 0} onClick={() => setOffset(current => current - 20)}>Предыдущие запросы</button>
      <button className="button secondary" disabled={!result.data || result.data.items.length < 20} onClick={() => setOffset(current => current + 20)}>Следующие запросы</button>
    </div>
  </section>;
}
