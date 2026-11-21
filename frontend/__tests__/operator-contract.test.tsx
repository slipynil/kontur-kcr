import { fireEvent, render, screen } from '@testing-library/react';
import { EventForm } from '../components/event-form';
import { ClientDetails } from '../components/client-details';
import { newRequestId } from '../lib/api';
import type { ClientCard, User } from '../lib/contracts';

jest.mock('next/navigation', () => ({ useRouter: () => ({ push: jest.fn() }) }));
const user: User = { id: 'operator', login: 'operator', role: 'OPERATOR', is_active: true, created_at: '' };
const assignmentId = '11111111-1111-4111-8111-111111111111';
const card: ClientCard = {
  kcr_code: 'A', inn: null, organization_name: 'Компания', tags: [],
  current_stage: 'IN_WORK', assignment_id: assignmentId, work_quarter: '2026_Q4',
  assignment: { operator_id: user.id, operator_name: user.login, assigned_at: '', work_quarter: '2026_Q4' },
  snapshots: [{ kcr_code: 'A', quarter: '2026_Q3', total_documents: 20, unsubmitted_documents: 10, snapshot_date: '2026-10-01' }],
  dynamics_quarter: '2026_Q4', dynamics_previous_quarter: '2026_Q3',
  dynamics_current_documents: null, dynamics_previous_documents: 10, quarter_summary: null,
};
const response = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data });
const onSaved = jest.fn();
beforeEach(() => {
  localStorage.clear(); jest.clearAllMocks();
  global.Headers = class { values: Record<string, string> = {}; set(key: string, value: string) { this.values[key.toLowerCase()] = value; } has(key: string) { return key.toLowerCase() in this.values; } } as unknown as typeof Headers;
  global.fetch = jest.fn(async () => response({ id: 'event', wait_applied: true })) as jest.Mock;
});

it('generates UUID v4 using browser crypto available over HTTP', () => {
  expect(newRequestId()).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  expect(newRequestId()).not.toBe(newRequestId());
});
it('keeps the original assignment after a reassignment and reports unapplied waiting', async () => {
  (fetch as jest.Mock).mockResolvedValue(response({ id: 'event', wait_applied: false }));
  const view = render(<EventForm card={card} user={user} base="/clients/A" call onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText('Комментарий'), { target: { value: 'Договорились' } });
  fireEvent.change(screen.getByLabelText('Обещанный срок'), { target: { value: '2099-01-20' } });
  fireEvent.change(screen.getByLabelText('Ожидание до'), { target: { value: '2099-01-15' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '2026_Q3' }));
  view.rerender(<EventForm card={{ ...card, assignment_id: 'new', assignment: { ...card.assignment!, operator_id: 'colleague' } }} user={user} base="/clients/A" call onSaved={onSaved} />);
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить событие' }));
  await screen.findByText('Звонок сохранён. Срок ожидания не применён, поскольку назначение изменилось.');
  const body = JSON.parse((fetch as jest.Mock).mock.calls[0][1].body);
  expect(body).toMatchObject({ event_type: 'CALL_RESULT', expected_assignment_id: assignmentId, work_quarter: '2026_Q4', call_result: 'AGREEMENT', promised_date: '2099-01-20', wait_until: '2099-01-15', document_quarters: ['2026_Q3'] });
  expect(body.request_id).toMatch(/^[a-f0-9-]{36}$/); expect(body).not.toHaveProperty('stage');
  expect(onSaved).toHaveBeenCalledTimes(1);
});
it('retries the identical call after a lost response', async () => {
  (fetch as jest.Mock).mockRejectedValueOnce(new Error('Сеть недоступна')).mockResolvedValueOnce(response({ id: 'event', wait_applied: false }));
  render(<EventForm card={card} user={user} base="/clients/A" call onSaved={onSaved} />);
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить событие' }));
  await screen.findByText('Сеть недоступна');
  expect(screen.getByLabelText('Комментарий')).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить событие' }));
  await screen.findByText('Событие сохранено');
  expect((fetch as jest.Mock).mock.calls[0][1].body).toBe((fetch as jest.Mock).mock.calls[1][1].body);
  expect(screen.getByLabelText('Комментарий')).toBeEnabled();
});
it('sends a historical comment with timezone and no call-only properties', async () => {
  render(<EventForm card={card} user={user} base="/clients/A" call={false} onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText('Комментарий'), { target: { value: 'История' } });
  fireEvent.change(screen.getByLabelText('Дата комментария'), { target: { value: '2020-01-01' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить событие' }));
  await screen.findByText('Событие сохранено');
  const body = JSON.parse((fetch as jest.Mock).mock.calls[0][1].body);
  expect(body).toMatchObject({ event_type: 'HISTORICAL_COMMENT', comment_text: 'История', document_quarters: [] });
  expect(body.historical_event_date).toBe('2019-12-31T21:00:00.000Z');
  expect(body).not.toHaveProperty('expected_assignment_id'); expect(body).not.toHaveProperty('stage');
  expect(screen.queryByRole('option', { name: 'Изменение этапа' })).not.toBeInTheDocument();
});
it('does not expose event creation to an administrator', () => {
  render(<EventForm card={card} user={{ ...user, role: 'ADMIN' }} base="/clients/A" call onSaved={onSaved} />);
  expect(screen.queryByRole('button', { name: 'Сохранить событие' })).not.toBeInTheDocument();
});
it('immediately takes over a colleague’s client with the current assignment ID', async () => {
  let reassigned = false;
  (fetch as jest.Mock).mockImplementation(async (_url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      reassigned = true;
      return response({ assignment_id: 'new-assignment', assigned_operator_id: user.id });
    }
    return response(reassigned ? { ...card, assignment_id: 'new-assignment' } : {
      ...card, assignment: { ...card.assignment, operator_id: 'colleague', operator_name: 'Коллега' },
    });
  });
  render(<ClientDetails code="A" user={user} call={false} />);
  expect(await screen.findByText('Коллега')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Перезакрепить на себя' }));
  await screen.findByText('Клиент перезакреплён на вас');
  expect(await screen.findByRole('button', { name: 'Записать звонок' })).toBeEnabled();
  const post = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(post[0]).toBe('/api/v1/clients/A/take?work_quarter=2026_Q4');
  expect(JSON.parse(post[1].body)).toMatchObject({ expected_assignment_id: assignmentId, request_id: expect.any(String) });
});
