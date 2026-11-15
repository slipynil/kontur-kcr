import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import Workspace from '../components/workspace';
import { ClientDetails } from '../components/clients';
import { Imports, Users } from '../components/management';
import { readSession, saveSession } from '../lib/api';
import type { User } from '../lib/contracts';
let pathname = '/';
let search = '';
const push = jest.fn(); const replace = jest.fn();
jest.mock('next/navigation', () => ({ usePathname: () => pathname, useRouter: () => ({ push, replace }), useSearchParams: () => new URLSearchParams(search) }));
const user: User = { id: 'operator-id', login: 'operator', role: 'OPERATOR', is_active: true, created_at: '2026-01-01' };
const page = { items: [], total: 0, page: 1, page_size: 20 };
const card = { kcr_code: 'A/B', inn: null, organization_name: 'Компания', tags: [], status: 'ACTIVE', current_stage: 'IN_WORK', assignment: { operator_id: 'another', operator_name: 'Другой оператор', work_quarter: '2026_Q3' }, work_quarter: '2026_Q3', snapshots: [], dynamics_quarter: '2026_Q4', dynamics_previous_quarter: '2026_Q3', dynamics_current_documents: null, dynamics_previous_documents: 0, quarter_summary: null };
const json = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data });
beforeEach(() => {
  localStorage.clear(); pathname = '/'; search = ''; jest.clearAllMocks();
  // jsdom does not expose the Fetch Headers class.
  global.Headers = class { values: Record<string, string> = {}; set(k: string, v: string) { this.values[k.toLowerCase()] = v; } get(k: string) { return this.values[k.toLowerCase()] ?? null; } has(k: string) { return k.toLowerCase() in this.values; } } as unknown as typeof Headers;
  global.fetch = jest.fn(async () => json(page)) as jest.Mock;
});
function signIn(role: User['role'] = 'OPERATOR') { saveSession({ token: 'jwt', token_type: 'Bearer', expires_in: 3600, user: { ...user, role } }); }
it('logs in with contract field names and no demo credentials', async () => {
  (fetch as jest.Mock).mockResolvedValue(json({ token: 'jwt', token_type: 'Bearer', expires_in: 3600, user }));
  render(<Workspace />);
  const login = await screen.findByLabelText('Логин'); expect(login).toHaveValue('');
  fireEvent.change(login, { target: { value: 'operator' } }); fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'secret' } });
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
  await waitFor(() => expect(readSession()?.user.role).toBe('OPERATOR'));
  expect((fetch as jest.Mock).mock.calls[0][0]).toBe('/api/v1/auth/login');
  expect(JSON.parse((fetch as jest.Mock).mock.calls[0][1].body)).toEqual({ login: 'operator', password: 'secret' });
});
it('restores operator access without exposing admin requests', async () => {
  signIn(); pathname = '/users'; render(<Workspace />);
  await screen.findByText('Раздел доступен только администратору.'); expect(fetch).not.toHaveBeenCalled();
});
it('applies server search and preserves zero numeric filters', async () => {
  signIn(); render(<Workspace />); await screen.findByText('Клиенты не найдены');
  fireEvent.change(screen.getByLabelText('Код КЦР или ИНН'), { target: { value: '6671%' } });
  fireEvent.change(screen.getByLabelText('Не передано от'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: 'Применить' }));
  await waitFor(() => expect((fetch as jest.Mock).mock.calls.at(-1)[0]).toContain('search=6671%25&min_unsubmitted_documents=0'));
  expect((fetch as jest.Mock).mock.calls[0][1].headers.get('Authorization')).toBe('Bearer jwt');
});
it('returns to login on an expired server session', async () => {
  signIn(); (fetch as jest.Mock).mockResolvedValue(json({ code: 'UNAUTHORIZED', message: 'Войдите снова' }, 401));
  render(<Workspace />); await screen.findByRole('button', { name: 'Войти' }); expect(readSession()).toBeNull();
});
function clientFetch(input: string, init?: RequestInit) {
  if (init?.method === 'POST') return Promise.resolve(json({ code: 'CLIENT_WAIT_DEADLINE_ACTIVE', message: 'Срок ещё не наступил' }, 409));
  return Promise.resolve(json(input.includes('/events') || input.includes('/history') ? page : card));
}
it('allows reassignment and sends the real work quarter, then displays 409', async () => {
  (fetch as jest.Mock).mockImplementation(clientFetch);
  render(<ClientDetails code="A/B" user={user} call={false} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Перезакрепить на себя' }));
  await screen.findByText('Срок ещё не наступил');
  expect((fetch as jest.Mock).mock.calls.some(([url, init]) => url === '/api/v1/clients/A%2FB/take?work_quarter=2026_Q3' && init.method === 'POST')).toBe(true);
  expect(screen.queryByText('Снять с оператора')).not.toBeInTheDocument();
});
it('protects active waiting across quarters', async () => {
  (fetch as jest.Mock).mockImplementation((url: string) => Promise.resolve(json(url.includes('/events') || url.includes('/history') ? page : { ...card, current_stage: 'WAIT_DEADLINE', wait_until: '2099-01-01' })));
  render(<ClientDetails code="A/B" user={user} call={false} />);
  expect(await screen.findByRole('button', { name: 'Перезакрепить на себя' })).toBeDisabled();
});
it('saves a call only after the API succeeds', async () => {
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) => init?.method === 'POST' ? Promise.resolve(json({ id: 'event' }, 201)) : clientFetch(url, init));
  render(<ClientDetails code="A/B" user={user} call />);
  fireEvent.change(await screen.findByLabelText('Комментарий'), { target: { value: 'Обещали передать' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить событие' })); await screen.findByText('Событие сохранено');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(call[0]).toBe('/api/v1/clients/A%2FB/events'); expect(JSON.parse(call[1].body)).toMatchObject({ event_type: 'CALL_RESULT', work_quarter: '2026_Q3', call_result: 'AGREEMENT', comment_text: 'Обещали передать' });
});
it('uploads the file and all four required multipart fields', async () => {
  (fetch as jest.Mock).mockResolvedValue(json({ id: 'batch-id' }, 202)); render(<Imports reportOnly={false} />);
  const file = new File(['xlsx'], 'clients.xlsx');
  fireEvent.change(screen.getByLabelText('Файл реестра .xlsx'), { target: { files: [file] } });
  fireEvent.change(screen.getByLabelText('Квартал'), { target: { value: '2025_Q2' } });
  fireEvent.change(screen.getByLabelText('Дата среза'), { target: { value: '2025-06-01' } });
  fireEvent.submit(screen.getByRole('button', { name: 'Загрузить' }).closest('form')!);
  await waitFor(() => expect(push).toHaveBeenCalledWith('/import-report?batch=batch-id'));
  const body = (fetch as jest.Mock).mock.calls[0][1].body as FormData;
  expect(body.get('file')).toBe(file); expect(body.get('selected_quarter')).toBe('2025_Q2'); expect(body.get('import_type')).toBe('REGISTRY'); expect(body.get('snapshot_date')).toBe('2025-06-01');
});


it('reuses the deletion operation ID after a lost response and accepts bodyless 200', async () => {
  jest.spyOn(window, 'confirm').mockReturnValue(true);
  let deletes = 0;
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'DELETE') {
      deletes++;
      return deletes === 1 ? Promise.reject(new Error('Соединение потеряно')) : Promise.resolve(json(undefined));
    }
    return clientFetch(url, init);
  });
  render(<ClientDetails code="A/B" user={{ ...user, role: 'ADMIN' }} call={false} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Удалить клиента' }));
  await screen.findByText('Соединение потеряно');
  fireEvent.click(screen.getByRole('button', { name: 'Удалить клиента' }));
  await waitFor(() => expect(push).toHaveBeenCalledWith('/'));
  const calls = (fetch as jest.Mock).mock.calls.filter(([, init]) => init.method === 'DELETE');
  expect(calls).toHaveLength(2); expect(calls[0][1].body).toBe(calls[1][1].body);
  expect(JSON.parse(calls[0][1].body).operation_id).toMatch(/^[0-9a-f-]{36}$/);
  jest.restoreAllMocks();
});
it('creates a user using a direct User response and refreshes the list', async () => {
  (fetch as jest.Mock).mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(json(init?.method === 'POST' ? user : page, init?.method === 'POST' ? 201 : 200)));
  render(<Users />); await screen.findByText('Страница 1 · Всего 0');
  fireEvent.change(screen.getByLabelText('Логин'), { target: { value: 'new.user' } });
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'secret' } });
  fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }));
  await screen.findByText('Пользователь создан');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(call[0]).toBe('/api/v1/users'); expect(JSON.parse(call[1].body)).toEqual({ login: 'new.user', password: 'secret', role: 'OPERATOR' });
  expect(screen.getByLabelText('Пароль')).toHaveValue('');
});
it('requires wait_until for a stage change and sends it separately from promised_date', async () => {
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) => init?.method === 'POST' ? Promise.resolve(json({ id: 'event' }, 201)) : clientFetch(url, init));
  render(<ClientDetails code="A/B" user={user} call={false} />);
  fireEvent.change(await screen.findByLabelText('Тип события'), { target: { value: 'STAGE_CHANGE' } });
  fireEvent.change(screen.getByLabelText('Новый этап'), { target: { value: 'WAIT_DEADLINE' } });
  expect(screen.getByLabelText('Ожидание до')).toBeRequired();
  fireEvent.change(screen.getByLabelText('Ожидание до'), { target: { value: '2027-01-15' } });
  fireEvent.change(screen.getByLabelText('Комментарий'), { target: { value: 'Ожидаем оригиналы' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить событие' }));
  await screen.findByText('Событие сохранено');
  const body = JSON.parse((fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST')[1].body);
  expect(body).toMatchObject({ event_type: 'STAGE_CHANGE', stage: 'WAIT_DEADLINE', wait_until: '2027-01-15', work_quarter: '2026_Q3' }); expect(body).not.toHaveProperty('promised_date');
});
it('rolls back a completed import with a recovery command', async () => {
  search = 'batch=batch-id'; jest.spyOn(window, 'confirm').mockReturnValue(true);
  const batch = { id: 'batch-id', filename: 'clients.xlsx', status: 'COMPLETED', selected_quarter: '2026_Q4', snapshot_date: '2026-11-10', total_rows: 10, success_count: 10, error_count: 0 };
  (fetch as jest.Mock).mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(json(init?.method === 'POST' ? { ...batch, status: 'ROLLED_BACK' } : batch)));
  render(<Imports reportOnly />); fireEvent.click(await screen.findByRole('button', { name: 'Откатить пакет' }));
  await screen.findByText('Пакет откачен');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(call[0]).toBe('/api/v1/imports/batch-id/rollback'); expect(JSON.parse(call[1].body).operation_id).toMatch(/^[0-9a-f-]{36}$/);
  jest.restoreAllMocks();
});
