import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import Workspace from '../components/workspace';
import { Clients } from '../components/clients';
import { ClientDetails } from '../components/client-details';
import { currentQuarter, QuarterInput } from '../components/api-ui';
import { Imports, Users } from '../components/management';
import { readSession, saveSession } from '../lib/api';
import type { User } from '../lib/contracts';
let pathname = '/';
let search = '';
const push = jest.fn(); const replace = jest.fn();
jest.mock('next/navigation', () => ({ usePathname: () => pathname, useRouter: () => ({ push, replace }), useSearchParams: () => new URLSearchParams(search) }));
const user: User = { id: 'operator-id', login: 'operator', role: 'OPERATOR', is_active: true, created_at: '2026-01-01' };
const page = { items: [], total: 0, page: 1, page_size: 20 };
const card = { assignment_id: '11111111-1111-4111-8111-111111111111', kcr_code: 'A/B', inn: null, organization_name: 'Компания', tags: [], current_stage: 'IN_WORK', assignment: { operator_id: 'another', operator_name: 'Другой оператор', work_quarter: '2026_Q3' }, work_quarter: '2026_Q3', snapshots: [], dynamics_quarter: '2026_Q4', dynamics_previous_quarter: '2026_Q3', dynamics_current_documents: null, dynamics_previous_documents: 0, quarter_summary: null };
const json = (data: unknown, status = 200) => ({ ok: status < 400, status, json: async () => data });
beforeEach(() => {
  localStorage.clear(); pathname = '/'; search = ''; jest.clearAllMocks();
  // jsdom does not expose the Fetch Headers class.
  global.Headers = class { values: Record<string, string> = {}; set(k: string, v: string) { this.values[k.toLowerCase()] = v; } get(k: string) { return this.values[k.toLowerCase()] ?? null; } has(k: string) { return k.toLowerCase() in this.values; } } as unknown as typeof Headers;
  global.fetch = jest.fn(async () => json(page)) as jest.Mock;
});
function signIn(role: User['role'] = 'OPERATOR') { saveSession({ token: 'jwt', token_type: 'Bearer', expires_in: 3600, user: { ...user, role } }); }

it('keeps the quarter input focused when opening suggestions and accepts typed text', () => {
  const onChange = jest.fn();
  render(<QuarterInput label="Квартал загрузки" value="" onChange={onChange} required />);
  const field = screen.getByRole('combobox', { name: 'Квартал' });
  field.focus();
  fireEvent.click(field);
  expect(screen.getByRole('listbox')).toBeVisible();
  expect(screen.getByRole('option', { name: currentQuarter() })).toBeVisible();
  expect(screen.queryByRole('option', { name: 'По умолчанию' })).not.toBeInTheDocument();
  expect(field).toHaveFocus();
  expect(field).toHaveAttribute('autocomplete', 'off');
  fireEvent.change(field, { target: { value: '2' } });
  expect(onChange).toHaveBeenCalledWith('2');
  expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  fireEvent.click(field);
  fireEvent.click(screen.getByRole('option', { name: currentQuarter() }));
  expect(onChange).toHaveBeenLastCalledWith(currentQuarter());
});

it('remembers an opened import report and shows its link on a later visit', async () => {
  signIn('ADMIN');
  const batch = {
    id: '3f2504e0-4f89-41d4-9a0c-0305e82c3301',
    filename: 'clients.xlsx',
    import_type: 'SNAPSHOT',
    selected_quarter: '2026_Q4',
    snapshot_date: '2026-11-18',
    status: 'FAILED',
    total_rows: 1,
    success_count: 0,
    error_count: 1,
  };
  search = `?batch=${batch.id}`;
  (fetch as jest.Mock).mockResolvedValue(json(batch));
  const view = render(<Imports reportOnly />);
  await screen.findByRole('heading', { name: batch.filename });
  view.unmount();

  search = '';
  render(<Imports reportOnly />);
  expect(screen.getByRole('link', { name: batch.filename }))
    .toHaveAttribute('href', `/import-report?batch=${batch.id}`);
});

it.each(['REGISTRY', 'SNAPSHOT'])('shows the spinner while a %s import is processing', async importType => {
  signIn('ADMIN');
  const batch = {
    id: '3f2504e0-4f89-41d4-9a0c-0305e82c3301', filename: 'clients.xlsx',
    import_type: importType, selected_quarter: '2026_Q4', snapshot_date: '2026-11-18',
    status: 'PROCESSING', total_rows: 1, success_count: 1, error_count: 0,
  };
  search = `?batch=${batch.id}`;
  (fetch as jest.Mock).mockResolvedValueOnce(json(batch)).mockResolvedValue(json({ ...batch, status: 'COMPLETED' }));

  render(<Imports reportOnly />);

  const processing = await screen.findByText(/Файл обрабатывается/);
  expect(processing).toHaveAttribute('role', 'status');
  expect(processing.querySelector('.feedback-spinner')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Обновить отчёт' }));
  expect(await screen.findByText('Завершён')).toBeVisible();
  expect(screen.queryByText(/Файл обрабатывается/)).not.toBeInTheDocument();
});

it('shows why a registry workbook was rejected when the error has no client or row', async () => {
  signIn('ADMIN');
  search = '?batch=3f2504e0-4f89-41d4-9a0c-0305e82c3301';
  (fetch as jest.Mock).mockResolvedValue(json({
    id: '3f2504e0-4f89-41d4-9a0c-0305e82c3301', filename: 'registry.xlsx',
    import_type: 'REGISTRY', status: 'FAILED', total_rows: 0, success_count: 0, error_count: 1,
    error_log: [{ sheet: '', row_number: 0, kcr_code: null, reason: 'Файл не удалось прочитать' }],
  }));

  render(<Imports reportOnly />);

  expect(await screen.findByText('Импорт завершился с ошибкой.')).toBeVisible();
  const table = screen.getByRole('table', { name: 'Ошибки импорта' });
  const row = within(table).getByRole('row', { name: /Файл не удалось прочитать/ });
  expect(within(row).getAllByRole('cell').map(cell => cell.textContent)).toEqual(['—', '—', '—', 'Файл не удалось прочитать']);
});

it('shows snapshot row errors even when other rows were imported', async () => {
  signIn('ADMIN');
  search = '?batch=3f2504e0-4f89-41d4-9a0c-0305e82c3301';
  const reason = 'Код КЦР отсутствует в реестре (client.kcr_code)';
  const errors = Array.from({ length: 21 }, (_, index) => ({
    sheet: '4 кв 2026', row_number: index + 2, kcr_code: String(index).padStart(5, '0'), reason,
  }));
  (fetch as jest.Mock).mockResolvedValue(json({
    id: '3f2504e0-4f89-41d4-9a0c-0305e82c3301', filename: 'snapshot.xlsx', selected_quarter: '2026_Q4', snapshot_date: '2026-11-18',
    import_type: 'SNAPSHOT', status: 'COMPLETED', total_rows: 22, success_count: 1, error_count: errors.length, error_log: errors,
  }));

  render(<Imports reportOnly />);

  expect(await screen.findByText('Завершён')).toBeVisible();
  const table = screen.getByRole('table', { name: 'Ошибки импорта' });
  expect(within(table).getByRole('row', { name: `4 кв 2026 2 00000 ${reason}` })).toBeVisible();
  expect(within(table).queryByText('00020')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Страница 2' }));
  expect(within(table).getByRole('row', { name: `4 кв 2026 22 00020 ${reason}` })).toBeVisible();
});

it('logs in with contract field names and no demo credentials', async () => {
  (fetch as jest.Mock).mockImplementation((url: string) => Promise.resolve(json(url.endsWith('/auth/login') ? { token: 'jwt', token_type: 'Bearer', expires_in: 3600, user } : page)));
  render(<Workspace />);
  const login = await screen.findByLabelText('Логин'); expect(login).toHaveValue('');
  fireEvent.change(login, { target: { value: 'operator' } }); fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'secret' } });
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }));
  await waitFor(() => expect(readSession()?.user.role).toBe('OPERATOR'));
  expect((fetch as jest.Mock).mock.calls[0][0]).toBe('/api/v1/auth/login');
  expect(JSON.parse((fetch as jest.Mock).mock.calls[0][1].body)).toEqual({ login: 'operator', password: 'secret' });
});
it('shows field validation on an empty login submit instead of calling the API', async () => {
  render(<Workspace />);
  fireEvent.click(await screen.findByRole('button', { name: 'Войти' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Введите логин');
  expect((fetch as jest.Mock).mock.calls.some(([url]) => String(url).includes('/auth/login'))).toBe(false);
});
it('restores operator access without exposing admin requests', async () => {
  signIn(); pathname = '/users'; render(<Workspace />);
  await screen.findByText('Раздел доступен только администратору.');
  expect(screen.getByRole('link', { name: 'Клиенты' })).toHaveAttribute('href', '/');
  expect(screen.queryByRole('button', { name: 'Запросы' })).not.toBeInTheDocument();
  expect((fetch as jest.Mock).mock.calls.some(([url]) => url.includes('/reassignment-notifications'))).toBe(false);
  expect((fetch as jest.Mock).mock.calls.some(([url]) => url.includes('/users'))).toBe(false);
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
it('searches while typing and removes a cleared numeric bound', async () => {
  signIn(); render(<Workspace />); await screen.findByText('Клиенты не найдены');
  const bound = screen.getByLabelText('Процент до');
  const submit = screen.getByRole('button', { name: 'Применить' });
  const lastQuery = () => new URL((fetch as jest.Mock).mock.calls.at(-1)[0], 'http://localhost').searchParams;
  fireEvent.change(bound, { target: { value: '0' } });
  fireEvent.click(submit);
  await waitFor(() => expect(lastQuery().get('max_unsubmitted_percent')).toBe('0'));
  fireEvent.change(screen.getByLabelText('Код КЦР или ИНН'), { target: { value: ' 6671 ' } });
  await waitFor(() => expect(lastQuery().get('search')).toBe('6671'));
  expect(lastQuery().get('max_unsubmitted_percent')).toBe('0');
  fireEvent.change(bound, { target: { value: '' } });
  fireEvent.click(submit);
  await waitFor(() => expect(lastQuery().has('max_unsubmitted_percent')).toBe(false));
});
function clientFetch(input: string, init?: RequestInit) {
  if (init?.method === 'POST') return Promise.resolve(json({ code: 'CLIENT_WAIT_DEADLINE_ACTIVE', message: 'Срок ещё не наступил' }, 409));
  return Promise.resolve(json(input.includes('/events') || input.includes('/history') ? page : card));
}

it('shows the Client History event date, author and details in API order', async () => {
  const occurredAt = '2026-08-15T09:30:00Z';
  const recordedAt = '2026-11-22T12:00:00Z';
  const event = {
    kcr_code: card.kcr_code, cycle_id: 'cycle-a', work_quarter: '2026_Q3',
    actor_id: null, actor_name: null, recorded_at: recordedAt,
    created_at: recordedAt, author_id: '00000000-0000-0000-0000-000000000000',
    comment_text: 'Устаревший текст', document_quarters: [],
  };
  const items = [
    { ...event, id: 'stage', seq: 4, event_type: 'STAGE_CHANGE', occurred_at: '2026-11-20T10:00:00Z',
      payload: { from_stage: 'WAIT_DEADLINE', to_stage: 'POOL', reason: 'WAIT_EXPIRED' } },
    { ...event, id: 'assignment', seq: 3, event_type: 'OPERATOR_ASSIGN', occurred_at: '2026-11-20T10:00:00Z',
      actor_id: card.assignment.operator_id, actor_name: card.assignment.operator_name,
      payload: { kind: 'REASSIGN', previous_operator_id: user.id, operator_id: card.assignment.operator_id } },
    { ...event, id: 'publication', seq: 2, event_type: 'PUBLICATION', occurred_at: '2026-10-01T10:00:00Z',
      work_quarter: '2026_Q4', cycle_id: 'cycle-b', actor_id: 'admin-id', actor_name: 'admin_at_publication', payload: {} },
    { ...event, id: 'note', seq: 1, event_type: 'HISTORICAL_COMMENT', occurred_at: occurredAt,
      actor_id: user.id, actor_name: 'historical_login', payload: { comment_text: 'Текст из хроники' } },
  ];
  (fetch as jest.Mock).mockImplementation(async (url: string) =>
    json(url.includes('/events') ? { ...page, items, total: items.length } : card));

  render(<ClientDetails code="A/B" user={user} call={false} />);
  fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Лента событий' }), { button: 0 });

  expect(await screen.findByText('Текст из хроники')).toBeVisible();
  const rows = within(screen.getByRole('tabpanel')).getAllByRole('listitem');
  expect(rows).toHaveLength(4);
  expect(rows[0]).toHaveTextContent('Этап: Ожидание срока → Пул');
  expect(rows[0]).not.toHaveTextContent(event.author_id);
  expect(rows[1]).toHaveTextContent(`Прежний ответственный: ${user.login}`);
  expect(rows[1]).toHaveTextContent(`Новый ответственный: ${card.assignment.operator_name}`);
  expect(rows[2]).toHaveTextContent('Передан в обзвон');
  expect(rows[2]).toHaveTextContent('admin_at_publication');
  expect(rows[3]).toHaveTextContent('historical_login');
  expect(within(rows[3]).getByText(/12:30 МСК/)).toHaveAttribute('datetime', occurredAt);
  expect(screen.getByRole('heading', { name: 'IV квартал 2026' })).toBeVisible();
  expect(screen.queryByText('Устаревший текст')).not.toBeInTheDocument();
});

it('still shows call details from the Operator Work event format', async () => {
  const item = { id: 'legacy-call', event_type: 'CALL_RESULT', work_quarter: '2026_Q3',
    comment_text: 'Приняли звонок', author_id: user.id, call_result: 'AGREEMENT', created_at: '2026-08-15T09:30:00Z' };
  const unknownAuthor = 'e78832bc-f2a2-4bef-9c5d-407d0334380b';
  const items = [item,
    { ...item, id: 'legacy-previous', author_id: 'previous-id', actor_name: 'previous_operator', comment_text: 'Предыдущий звонок' },
    { ...item, id: 'legacy-other', author_id: unknownAuthor, comment_text: 'Звонок без имени автора' },
  ];
  (fetch as jest.Mock).mockImplementation(async (url: string) =>
    json(url.includes('/events') ? { ...page, items, total: items.length } : card));

  render(<ClientDetails code="A/B" user={user} call={false} />);
  fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Лента событий' }), { button: 0 });

  const [row, older, unknown] = await within(screen.getByRole('tabpanel')).findAllByRole('listitem');
  expect(row).toHaveTextContent('Приняли звонок');
  expect(row).toHaveTextContent('Есть договорённость');
  expect(row).toHaveTextContent(user.login);
  expect(within(row).getByRole('heading', { name: 'Результат звонка' })).toBeVisible();
  expect(older).toHaveTextContent('Предыдущий звонок');
  expect(older).toHaveTextContent('previous_operator');
  expect(unknown).not.toHaveTextContent(unknownAuthor);
});

it('takes a free client using the work cycle returned by Gateway', async () => {
  const workQuarter = '2025_Q3';
  const assignedAt = '2025-09-01T00:00:00Z';
  const assignmentId = 'new-assignment';
  const profile = { ...card, work_quarter: workQuarter, current_stage: 'POOL', assignment: null, assignment_id: null };
  let assigned = false;

  (fetch as jest.Mock).mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === 'POST') {
      assigned = true;
      return json({ kcr_code: 'A/B', assignment_id: assignmentId, assigned_operator_id: user.id, work_quarter: workQuarter, assigned_at: assignedAt });
    }
    if (url.includes('/events') || url.includes('/history')) return json(page);
    return json(assigned ? {
      ...profile,
      current_stage: 'IN_WORK',
      assignment_id: assignmentId,
      assignment: { operator_id: user.id, operator_name: user.login, work_quarter: workQuarter, assigned_at: assignedAt },
    } : profile);
  });

  render(<ClientDetails code="A/B" user={user} call={false} />);
  const button = await screen.findByRole('button', { name: 'Взять в работу' });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  await screen.findByText(user.login, { exact: true });
  const [url, init] = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(url).toBe('/api/v1/clients/A%2FB/take?work_quarter=2025_Q3');
  expect(JSON.parse(init.body).expected_assignment_id).toBeNull();
  expect((fetch as jest.Mock).mock.calls.some(([url]) => url.includes('/pool?'))).toBe(false);
});

it('disables taking when Gateway reports no published work cycle', async () => {
  (fetch as jest.Mock).mockImplementation(async (url: string) =>
    json(url.includes('/events') || url.includes('/history') ? page : {
      ...card, work_quarter: null, assignment_id: null, assignment: null, current_stage: null,
    }),
  );
  render(<ClientDetails code="A/B" user={user} call={false} />);
  expect(await screen.findByRole('button', { name: 'Взять в работу' })).toBeDisabled();
  expect(screen.getByText('Для клиента пока нет опубликованного цикла обзвона.')).toBeVisible();
});
const list = (codes: string[]) => ({
  items: codes.map(code => ({ kcr_code: code, inn: null, organization_name: `Организация ${code}`, tags: [], current_stage: 'POOL', assigned_operator_id: null, dynamics: null, latest_snapshot: null, dynamics_quarter: '2026_Q4', dynamics_previous_quarter: '2026_Q3', dynamics_current_documents: null, dynamics_previous_documents: null, quarter_summary: null })),
  page: 1, page_size: 20, total: codes.length,
});
it('publishes the selected registry clients for the chosen quarter', async () => {
  const page = list(['E2E-1', 'E2E-2']);
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) =>
    init?.method === 'PUT' ? Promise.resolve(json(undefined, 204)) : Promise.resolve(json({
      ...page,
      items: [{ ...page.items[0], assigned_operator_id: 'operator-id', operator_name: 'operator-login' }, page.items[1]],
    })));
  render(<Clients user={{ ...user, role: 'ADMIN' }} />);
  fireEvent.click(await screen.findByRole('checkbox', { name: 'Выбрать E2E-1' }));
  expect(screen.getByRole('cell', { name: 'operator-login' })).toBeVisible();
  expect(screen.getByRole('checkbox', { name: 'Выбрать всех на странице' })).toBePartiallyChecked();
  fireEvent.click(screen.getByRole('button', { name: 'Публикация на обзвон' }));
  fireEvent.change(screen.getByLabelText('Квартал обзвона'), { target: { value: '2026_Q4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Опубликовать выбранных' }));
  await screen.findByText('Опубликовано клиентов: 1');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'PUT');
  expect(call[0]).toBe('/api/v1/pool/publication?work_quarter=2026_Q4');
  expect(JSON.parse(call[1].body)).toEqual({ codes: ['E2E-1'] });
});
it('selects and publishes filtered clients across pages, except manually unchecked rows', async () => {
  const codes = Array.from({ length: 101 }, (_, index) => `VIP-${index}`);
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return Promise.resolve(json(undefined, 204));
    const params = new URL(url, 'http://localhost').searchParams;
    const matches = params.get('tags') === 'VIP' ? codes : ['OTHER', ...codes];
    const page = Number(params.get('page'));
    const size = Number(params.get('page_size'));
    return Promise.resolve(json({
      ...list(matches.slice((page - 1) * size, page * size)), total: matches.length, page, page_size: size,
    }));
  });
  render(<Clients user={{ ...user, role: 'ADMIN' }} />);
  fireEvent.change(screen.getByLabelText('Статус клиента'), { target: { value: 'VIP' } });
  fireEvent.click(screen.getByRole('button', { name: 'Применить' }));
  await screen.findByRole('checkbox', { name: 'Выбрать VIP-0' });
  fireEvent.click(screen.getByRole('button', { name: 'Выбрать всех по фильтрам' }));
  await screen.findByText('Выбрано клиентов: 101');
  fireEvent.click(screen.getByRole('button', { name: 'Страница 2' }));
  const checkbox = await screen.findByRole('checkbox', { name: 'Выбрать VIP-20' });
  expect(checkbox).toBeChecked();
  fireEvent.click(checkbox);
  fireEvent.click(screen.getByRole('button', { name: 'Публикация на обзвон' }));
  fireEvent.change(screen.getByLabelText('Квартал обзвона'), { target: { value: '2026_Q4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Опубликовать выбранных' }));
  await screen.findByText('Опубликовано клиентов: 100');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'PUT');
  expect(JSON.parse(call[1].body)).toEqual({ codes: codes.filter(code => code !== 'VIP-20') });
});
it('publishes the whole registry across pages and respects the API batch limit', async () => {
  const codes = Array.from({ length: 1001 }, (_, index) => `CLIENT-${index}`);
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return Promise.resolve(json(undefined, 204));
    const params = new URL(url, 'http://localhost').searchParams;
    const size = Number(params.get('page_size'));
    const page = Number(params.get('page'));
    return Promise.resolve(json({ ...list(codes.slice((page - 1) * size, page * size)), total: codes.length, page, page_size: size }));
  });
  render(<Clients user={{ ...user, role: 'ADMIN' }} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Публикация на обзвон' }));
  fireEvent.change(await screen.findByLabelText('Квартал обзвона'), { target: { value: '2026_Q4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Опубликовать весь реестр' }));
  const dialog = await screen.findByRole('dialog', { name: 'Опубликовать весь реестр?' });
  expect(dialog).toHaveTextContent('IV квартал 2026');
  expect((fetch as jest.Mock).mock.calls.some(([, init]) => init.method === 'PUT')).toBe(false);
  fireEvent.click(within(dialog).getByRole('button', { name: 'Опубликовать всех' }));
  await screen.findByText('Опубликовано клиентов: 1001');
  const batches = (fetch as jest.Mock).mock.calls.filter(([, init]) => init.method === 'PUT')
    .map(([, init]) => JSON.parse(init.body).codes);
  expect(batches.map(batch => batch.length)).toEqual([1000, 1]);
  expect(batches.flat()).toEqual(codes);
});
it('requires a selection and a quarter before publishing the registry', async () => {
  (fetch as jest.Mock).mockResolvedValue(json(list(['E2E-1'])));
  render(<Clients user={{ ...user, role: 'ADMIN' }} />);
  await screen.findByRole('checkbox', { name: 'Выбрать E2E-1' });
  fireEvent.click(screen.getByRole('button', { name: 'Публикация на обзвон' }));
  fireEvent.click(screen.getByRole('button', { name: 'Опубликовать выбранных' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Выберите хотя бы одного клиента');
  fireEvent.click(screen.getByRole('button', { name: 'Закрыть' }));
  fireEvent.click(screen.getByRole('checkbox', { name: 'Выбрать E2E-1' }));
  fireEvent.click(screen.getByRole('button', { name: 'Публикация на обзвон' }));
  fireEvent.change(screen.getByLabelText('Квартал обзвона'), { target: { value: '2026-Q4' } });
  fireEvent.click(screen.getByRole('button', { name: 'Опубликовать выбранных' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Квартал обзвона должен быть в формате 2026_Q4');
  expect((fetch as jest.Mock).mock.calls.some(([, init]) => init.method === 'PUT')).toBe(false);
});
it('publishes a single client from the card', async () => {
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) =>
    init?.method === 'PUT' ? Promise.resolve(json(undefined, 204)) : clientFetch(url, init));
  render(<ClientDetails code="A/B" user={{ ...user, role: 'ADMIN' }} call={false} />);
  fireEvent.click(await screen.findByRole('button', { name: 'На обзвон' }));
  fireEvent.change(await screen.findByLabelText('Квартал обзвона'), { target: { value: '2026_Q3' } });
  fireEvent.click(screen.getByRole('button', { name: 'Опубликовать клиента' }));
  await screen.findByText('Клиент опубликован');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'PUT');
  expect(call[0]).toBe('/api/v1/clients/A%2FB/publication?work_quarter=2026_Q3');
  expect(call[1].body).toBeUndefined();
});
it('takes only a free client and reports a conflict when its assignment changed', async () => {
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) =>
    init?.method === 'POST'
      ? Promise.resolve(json({ code: 'ASSIGNMENT_CHANGED', message: 'Ответственный изменился. Обновите карточку' }, 409))
      : Promise.resolve(json({ ...card, assignment: null, assignment_id: null })));
  render(<ClientDetails code="A/B" user={user} call={false} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Взять в работу' }));
  await screen.findByText('Ответственный изменился. Обновите карточку');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(call[0]).toBe('/api/v1/clients/A%2FB/take?work_quarter=2026_Q3');
  expect(JSON.parse(call[1].body).expected_assignment_id).toBeNull();
  expect(screen.queryByText('Снять с оператора')).not.toBeInTheDocument();
});
it('protects active waiting across quarters', async () => {
  (fetch as jest.Mock).mockImplementation((url: string) => Promise.resolve(json(url.includes('/events') || url.includes('/history') ? page : { ...card, current_stage: 'WAIT_DEADLINE', wait_until: '2099-01-01' })));
  render(<ClientDetails code="A/B" user={user} call={false} />);
  expect(await screen.findByText(/Ожидание защищает ответственного до/)).toBeVisible();
  expect(screen.getByRole('button', { name: 'Перезакрепить на себя' })).toBeDisabled();
});
it('saves a call only after the API succeeds', async () => {
  (fetch as jest.Mock).mockImplementation((url: string, init?: RequestInit) => init?.method === 'POST' ? Promise.resolve(json({ id: 'event', wait_applied: true }, 201)) : Promise.resolve(json(url.includes('/events') || url.includes('/history') ? page : { ...card, assignment: { ...card.assignment, operator_id: user.id } })));
  render(<ClientDetails code="A/B" user={user} call />);
  fireEvent.change(await screen.findByLabelText('Комментарий'), { target: { value: 'Обещали передать' } });
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить событие' })); await screen.findByText('Событие сохранено');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(call[0]).toBe('/api/v1/clients/A%2FB/events'); expect(JSON.parse(call[1].body)).toMatchObject({ event_type: 'CALL_RESULT', work_quarter: '2026_Q3', call_result: 'AGREEMENT', comment_text: 'Обещали передать' });
});
it.each(['REGISTRY', 'SNAPSHOT'])('uploads %s with only its multipart fields', async type => {
  (fetch as jest.Mock).mockResolvedValue(json({ id: 'batch-id' }, 202)); render(<Imports reportOnly={false} />);
  const file = new File(['xlsx'], 'clients.xlsx');
  fireEvent.change(screen.getByLabelText('Файл реестра .xlsx'), { target: { files: [file] } });
  expect(screen.queryByLabelText('Квартал')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Дата среза')).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Тип импорта'), { target: { value: 'SNAPSHOT' } });
  fireEvent.change(screen.getByLabelText('Квартал'), { target: { value: '2025_Q2' } });
  fireEvent.change(screen.getByLabelText('Дата среза'), { target: { value: '2025-06-01' } });
  if (type === 'REGISTRY') {
    fireEvent.change(screen.getByLabelText('Тип импорта'), { target: { value: type } });
    expect(screen.queryByLabelText('Квартал')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Дата среза')).not.toBeInTheDocument();
  }
  fireEvent.submit(screen.getByRole('button', { name: 'Загрузить' }).closest('form')!);
  await waitFor(() => expect(push).toHaveBeenCalledWith('/import-report?batch=batch-id'));
  const body = (fetch as jest.Mock).mock.calls[0][1].body as FormData;
  expect(body.get('file')).toBe(file);
  expect(body.get('import_type')).toBe(type);
  expect(body.get('selected_quarter')).toBe(type === 'SNAPSHOT' ? '2025_Q2' : null);
  expect(body.get('snapshot_date')).toBe(type === 'SNAPSHOT' ? '2025-06-01' : null);
});

it('clears snapshot metadata when switching to a registry upload', () => {
  render(<Imports reportOnly={false} />);
  const type = screen.getByLabelText('Тип импорта');
  fireEvent.change(type, { target: { value: 'SNAPSHOT' } });
  fireEvent.change(screen.getByLabelText('Квартал'), { target: { value: '2025_Q2' } });
  fireEvent.change(screen.getByLabelText('Дата среза'), { target: { value: '2025-06-01' } });
  fireEvent.change(type, { target: { value: 'REGISTRY' } });
  expect(screen.queryByLabelText('Квартал')).not.toBeInTheDocument();
  expect(screen.queryByLabelText('Дата среза')).not.toBeInTheDocument();
  fireEvent.change(type, { target: { value: 'SNAPSHOT' } });
  expect(screen.getByLabelText('Квартал')).toHaveValue('');
  expect(screen.getByLabelText('Дата среза')).toHaveValue('');
});

it('shows a visible quarter format error instead of silently blocking submit', async () => {
  render(<Imports reportOnly={false} />);
  fireEvent.change(screen.getByLabelText('Файл реестра .xlsx'), { target: { files: [new File(['xlsx'], 'clients.xlsx')] } });
  fireEvent.change(screen.getByLabelText('Тип импорта'), { target: { value: 'SNAPSHOT' } });
  fireEvent.change(screen.getByLabelText('Квартал'), { target: { value: '2025-Q2' } });
  fireEvent.change(screen.getByLabelText('Дата среза'), { target: { value: '2025-06-01' } });
  fireEvent.submit(screen.getByRole('button', { name: 'Загрузить' }).closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('Квартал загрузки должен быть в формате 2026_Q4');
  expect(fetch).not.toHaveBeenCalled();
});

it('requires a package ID before opening the report', async () => {
  render(<Imports reportOnly />);
  fireEvent.submit(screen.getByRole('button', { name: 'Открыть отчёт' }).closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('Введите ID пакета');
  expect(push).not.toHaveBeenCalled();
});

it('rejects a package ID that is not a UUID without leaving the form', async () => {
  render(<Imports reportOnly />);
  fireEvent.change(screen.getByLabelText('ID пакета'), { target: { value: '1' } });
  fireEvent.submit(screen.getByRole('button', { name: 'Открыть отчёт' }).closest('form')!);
  expect(await screen.findByRole('alert')).toHaveTextContent('ID пакета должен быть UUID');
  expect(push).not.toHaveBeenCalled();
});

it('opens the report for a UUID package ID', async () => {
  render(<Imports reportOnly />);
  fireEvent.change(screen.getByLabelText('ID пакета'), { target: { value: '3f2504e0-4f89-41d4-9a0c-0305e82c3301' } });
  fireEvent.submit(screen.getByRole('button', { name: 'Открыть отчёт' }).closest('form')!);
  await waitFor(() => expect(push).toHaveBeenCalledWith('/import-report?batch=3f2504e0-4f89-41d4-9a0c-0305e82c3301'));
});

it('reuses the deletion operation ID after a lost response and accepts bodyless 200', async () => {
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
  const dialog = await screen.findByRole('dialog', { name: 'Удалить клиента?' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Удалить клиента' }));
  await screen.findByText('Соединение потеряно');
  fireEvent.click(within(dialog).getByRole('button', { name: 'Удалить клиента' }));
  await waitFor(() => expect(push).toHaveBeenCalledWith('/'));
  const calls = (fetch as jest.Mock).mock.calls.filter(([, init]) => init.method === 'DELETE');
  expect(calls).toHaveLength(2); expect(calls[0][1].body).toBe(calls[1][1].body);
  expect(JSON.parse(calls[0][1].body).operation_id).toMatch(/^[0-9a-f-]{36}$/);
  jest.restoreAllMocks();
});
it('creates a user using a direct User response and refreshes the list', async () => {
  (fetch as jest.Mock).mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(json(init?.method === 'POST' ? user : page, init?.method === 'POST' ? 201 : 200)));
  render(<Users />); await screen.findByText('Пользователи не найдены');
  fireEvent.click(screen.getByRole('button', { name: 'Добавить пользователя' }));
  fireEvent.change(screen.getByLabelText('Логин'), { target: { value: 'new.user' } });
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'secret' } });
  fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }));
  await screen.findByText('Пользователь создан');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(call[0]).toBe('/api/v1/users'); expect(JSON.parse(call[1].body)).toEqual({ login: 'new.user', password: 'secret', role: 'OPERATOR' });
  fireEvent.click(screen.getByRole('button', { name: 'Добавить пользователя' }));
  expect(screen.getByLabelText('Пароль')).toHaveValue('');
});
it('shows field validation on an empty user submit instead of calling the API', async () => {
  render(<Users />);
  fireEvent.click(screen.getByRole('button', { name: 'Добавить пользователя' }));
  fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Введите логин');
  expect((fetch as jest.Mock).mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false);
});
it('rolls back a completed import with a recovery command', async () => {
  search = 'batch=batch-id';
  const batch = { id: 'batch-id', filename: 'clients.xlsx', status: 'COMPLETED', import_type: 'REGISTRY', total_rows: 10, success_count: 10, error_count: 0 };
  (fetch as jest.Mock).mockImplementation((_url: string, init?: RequestInit) => Promise.resolve(json(init?.method === 'POST' ? { ...batch, status: 'ROLLED_BACK' } : batch)));
  render(<Imports reportOnly />); fireEvent.click(await screen.findByRole('button', { name: 'Откатить пакет' }));
  const dialog = await screen.findByRole('dialog', { name: 'Откатить пакет?' });
  expect(within(dialog).queryByText('Квартал загрузки')).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button', { name: 'Откатить пакет' }));
  await screen.findByText('Пакет откачен');
  const call = (fetch as jest.Mock).mock.calls.find(([, init]) => init.method === 'POST');
  expect(call[0]).toBe('/api/v1/imports/batch-id/rollback'); expect(JSON.parse(call[1].body).operation_id).toMatch(/^[0-9a-f-]{36}$/);
  jest.restoreAllMocks();
});
