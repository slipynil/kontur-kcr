import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import Workspace from '../components/workspace'

const nav = { push: jest.fn(), replace: jest.fn() }
let pathname = '/'
jest.mock('next/navigation', () => ({ useRouter: () => nav, usePathname: () => pathname }))

function json(data: unknown, status = 200) { return { ok: status < 400, status, json: async () => data } }
beforeEach(() => {
  pathname = '/'; nav.push.mockClear(); nav.replace.mockClear()
  sessionStorage.clear()
  global.fetch = jest.fn(async input => String(input).includes('/api/auth') ? json({ user: { username: 'vera.v', name: 'Вера В.', role: 'Администратор' } }) : json({ users: [] })) as jest.Mock
})

it('shows loading then login, displays errors and accepts successful sign-in', async () => {
  ;(global.fetch as jest.Mock).mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({}, 401)).mockResolvedValueOnce(json({ user: { role: 'Администратор' } }))
  render(<Workspace />)
  await screen.findByRole('heading', { name: 'Контур КЦР' })
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }))
  expect(await screen.findByText('Неверный логин или пароль')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Логин'), { target: { value: 'vera.v' } })
  fireEvent.click(screen.getByRole('button', { name: 'Войти' }))
  await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/'))
})

it('filters the client table, resets filters, and navigates into a client', async () => {
  render(<Workspace />)
  await screen.findByRole('heading', { name: 'Клиенты' })
  fireEvent.change(screen.getByPlaceholderText(/Код КЦР или ИНН/), { target: { value: 'AAA02210' } })
  expect(screen.getByText('ИП Гаврилов С. А.')).toBeInTheDocument()
  expect(screen.queryByText('ООО «Северный терминал»')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Сбросить' }))
  expect(screen.getByText('ООО «Северный терминал»')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Все динамики' }))
  fireEvent.click(screen.getByRole('option', { name: 'Растёт' }))
  expect(screen.getByText('ООО «Северный терминал»')).toBeInTheDocument()
  fireEvent.click(screen.getByText('AAA00412'))
  expect(nav.push).toHaveBeenCalledWith('/client/AAA00412')
})

it('supports upload selection and navigation to the import report', async () => {
  pathname = '/upload'
  render(<Workspace />)
  await screen.findByRole('heading', { name: 'Загрузка реестра' })
  const input = document.querySelector('input[type="file"]')!
  fireEvent.change(input, { target: { files: [new File(['data'], 'registry.xlsx')] } })
  expect(screen.getByText('registry.xlsx')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Отменить' }))
  expect(screen.queryByText('registry.xlsx')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Загрузить' }))
  expect(nav.push).toHaveBeenCalledWith('/import-report')
})

it('opens and dismisses the add-user modal', async () => {
  pathname = '/users'
  render(<Workspace />)
  await screen.findByRole('heading', { name: 'Пользователи и роли' })
  fireEvent.click(screen.getByRole('button', { name: 'Добавить пользователя' }))
  expect(screen.getByRole('heading', { name: 'Добавить пользователя' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Отмена' }))
  await waitFor(() => expect(screen.queryByRole('heading', { name: 'Добавить пользователя' })).not.toBeInTheDocument())
})

it('toggles status rule checkboxes and selects a role option', async () => {
  pathname = '/statuses'
  render(<Workspace />)
  await screen.findByRole('heading', { name: 'Справочник статусов' })
  fireEvent.click(screen.getAllByRole('button', { name: 'Изменить' })[0])
  expect(screen.getByText('Редактирование статуса')).toBeInTheDocument()
  cleanup()
  pathname = '/users'
  ;(global.fetch as jest.Mock).mockImplementation(async input => String(input).includes('/api/auth') ? json({ user: { username: 'vera.v', name: 'Вера В.', role: 'Администратор' } }) : json({ users: [{ username: 'operator', name: 'Анна К.', role: 'Оператор', active: true, clients: 0 }] }))
  render(<Workspace />)
  await screen.findByRole('heading', { name: 'Пользователи и роли' })
  await screen.findByText('operator')
  const roleSelect = screen.getByRole('button', { name: 'Оператор' })
  fireEvent.click(roleSelect)
  expect(screen.getAllByRole('option').length).toBe(2)
})

const code = 'AAA00412'
const clientApi = '/api/clients/' + code
const workApi = '/api/operator-work'
const serviceAssignment = {
  code, owner: 'Олег П.', operatorWork: true, published: true,
  assignmentId: 'original-assignment', workQuarter: '2026_Q4',
  stage: 'WAIT_DEADLINE', waitUntil: '2026-11-25',
}

function workspaceRequests(role: 'Администратор' | 'Оператор', routes: Record<string, jest.Mock>) {
  const fetchMock = jest.mocked(globalThis.fetch)
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input)
    const handler = routes[(init?.method ?? 'GET') + ' ' + url]
    if (handler) return handler(init)
    if (url === '/api/auth') return json({ user: { role } }) as Response
    if (url === '/api/users') return json({}, 403) as Response
    if (url === workApi + '?pool') return json({ enabled: true, items: [] }) as Response
    return json({ items: [], total: 0, page_size: 20 }) as Response
  })
  return fetchMock
}

it('restricts the operator registry to published clients and uses their current owners', async () => {
  workspaceRequests('Оператор', {
    ['GET ' + workApi + '?pool']: jest.fn().mockResolvedValue(json({
      enabled: true, items: [{ code, owner: 'Олег П.' }],
    })),
  })
  render(<Workspace />)
  expect(await screen.findByText('Олег П.')).toBeInTheDocument()
  expect(screen.getByText(code)).toBeInTheDocument()
  expect(screen.queryByText('AAA02210')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Загрузка реестра/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByText(code))
  expect(nav.push).toHaveBeenCalledWith('/client/' + code)
})

it('redirects an operator away from admin pages and opens clients from the local queue', async () => {
  pathname = '/users'
  workspaceRequests('Оператор', {
    ['GET ' + workApi + '?pool']: jest.fn().mockResolvedValue(json({ items: [] })),
  })
  const { rerender } = render(<Workspace />)
  await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/today'))
  pathname = '/today'
  rerender(<Workspace />)
  expect(await screen.findByRole('heading', { name: /Свободные из списка на обзвон/ })).toBeInTheDocument()
  fireEvent.click((await screen.findAllByText(code))[0])
  expect(nav.push).toHaveBeenCalledWith('/client/' + code)
})

it('keeps the same request ID after losing the response to a reassignment request', async () => {
  pathname = '/client/' + code
  const claim = jest.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(json({}, 202))
  workspaceRequests('Оператор', {
    ['GET ' + clientApi]: jest.fn().mockResolvedValue(json(serviceAssignment)),
    ['POST ' + clientApi + '/claim']: claim,
  })
  render(<Workspace />)
  const take = await screen.findByRole('button', { name: 'Перезакрепить на себя' })
  expect(screen.getByText('2026-11-25')).toBeInTheDocument()
  fireEvent.click(take)
  expect(await screen.findByText('Не удалось отправить запрос. Повторите попытку.')).toBeInTheDocument()
  fireEvent.click(take)
  expect(await screen.findByText('Запрос отправлен. У ответственного есть час, чтобы отказаться.')).toBeInTheDocument()
  expect(JSON.parse(claim.mock.calls[0][0].body)).toEqual({
    expected_assignment_id: 'original-assignment', request_id: expect.any(String),
  })
  expect(claim.mock.calls[0][0].body).toBe(claim.mock.calls[1][0].body)
  expect(nav.push).not.toHaveBeenCalledWith('/call/' + code)
})

it('reports a take conflict and opens the call after a successful take', async () => {
  pathname = '/client/' + code
  const claim = jest.fn().mockResolvedValueOnce(json({ message: 'Назначение изменилось' }, 409))
    .mockResolvedValueOnce(json({ owner: 'Анна К.' }))
  workspaceRequests('Оператор', {
    ['GET ' + clientApi]: jest.fn().mockResolvedValue(json({
      ...serviceAssignment, owner: null, assignmentId: null, stage: 'POOL', waitUntil: null,
    })),
    ['POST ' + clientApi + '/claim']: claim,
  })
  render(<Workspace />)
  const take = await screen.findByRole('button', { name: 'Взять в работу' })
  fireEvent.click(take)
  expect(await screen.findByText('Назначение изменилось')).toBeInTheDocument()
  fireEvent.click(take)
  expect(await screen.findByText('Клиент взят в работу')).toBeInTheDocument()
  expect(nav.push).toHaveBeenCalledWith('/call/' + code)
  const [first, second] = claim.mock.calls.map(([init]) => JSON.parse(init.body))
  expect(first.expected_assignment_id).toBeNull()
  expect(second.request_id).not.toBe(first.request_id)
})

it.each([
  [{ published: false }, 'Клиент вне пула'],
  [{ awaitingBalance: true }, 'Срок истёк · вне пула'],
  [{ operatorWork: false }, 'Клиент занят'],
])('disables take when the assignment cannot be taken (%s)', async (state, label) => {
  pathname = '/client/' + code
  workspaceRequests('Оператор', {
    ['GET ' + clientApi]: jest.fn().mockResolvedValue(json({ ...serviceAssignment, ...state })),
  })
  render(<Workspace />)
  expect(await screen.findByRole('button', { name: label })).toBeDisabled()
})

it('opens the current operator call and returns to the client after saving', async () => {
  pathname = '/client/' + code
  const save = jest.fn().mockResolvedValue(json({ wait_applied: false }, 201))
  const fetchMock = workspaceRequests('Оператор', {
    ['GET ' + clientApi]: jest.fn().mockResolvedValue(json({ ...serviceAssignment, ownerIsMe: true })),
    ['POST ' + clientApi + '/events']: save,
  })
  const { rerender } = render(<Workspace />)
  fireEvent.click(await screen.findByRole('button', { name: 'Открыть звонок' }))
  expect(nav.push).toHaveBeenCalledWith('/call/' + code)
  expect(fetchMock).not.toHaveBeenCalledWith(clientApi + '/claim', expect.anything())
  pathname = '/call/' + code
  rerender(<Workspace />)
  fireEvent.click(await screen.findByRole('button', { name: 'Сохранить звонок' }))
  expect(await screen.findByText('Звонок сохранён')).toBeInTheDocument()
  expect(nav.push).toHaveBeenLastCalledWith('/client/' + code)
})

it('publishes the selected clients together and preserves selection when the request fails', async () => {
  pathname = '/call-list'
  const publication = jest.fn().mockResolvedValueOnce(json({ message: 'Конфликт публикации' }, 409))
    .mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(json({ ok: true }))
  workspaceRequests('Администратор', { ['POST ' + workApi]: publication })
  render(<Workspace />)
  const publish = await screen.findByRole('button', { name: 'Передать операторам' })
  const first = screen.getByRole('checkbox', { name: 'Выбрать ' + code })
  const second = screen.getByRole('checkbox', { name: 'Выбрать AAA02210' })
  expect(publish).toBeDisabled()
  fireEvent.click(first)
  fireEvent.click(second)
  fireEvent.click(second)
  expect(screen.getByText('Выбрано клиентов: 1')).toBeInTheDocument()
  fireEvent.click(second)
  fireEvent.click(publish)
  expect(screen.getByRole('button', { name: 'Публикуем…' })).toBeDisabled()
  expect(await screen.findByRole('alert')).toHaveTextContent('Конфликт публикации')
  expect(first).toBeChecked()
  expect(second).toBeChecked()
  fireEvent.click(publish)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось подтвердить публикацию')
  fireEvent.click(publish)
  expect(await screen.findByRole('status')).toHaveTextContent('Список опубликован')
  expect(first).not.toBeChecked()
  expect(second).not.toBeChecked()
  expect(publish).toBeDisabled()
  expect(publication.mock.calls.map(([init]) => JSON.parse(init.body))).toEqual(
    Array(3).fill({ codes: [code, 'AAA02210'] }),
  )
})

it('lets an administrator add a user after correcting an error', async () => {
  pathname = '/users'
  const user = { username: 'ivan.s', name: 'Иван С.', role: 'Администратор', active: true, clients: 0 }
  const createUser = jest.fn().mockResolvedValueOnce(json({ error: 'Логин занят' }, 409))
    .mockResolvedValueOnce(json({ user }, 201))
  workspaceRequests('Администратор', {
    'GET /api/users': jest.fn().mockResolvedValue(json({ users: [] })),
    'POST /api/users': createUser,
  })
  render(<Workspace />)
  fireEvent.click(await screen.findByRole('button', { name: 'Добавить пользователя' }))
  fireEvent.change(screen.getByLabelText('Логин'), { target: { value: 'old.login' } })
  fireEvent.change(screen.getByLabelText('Имя пользователя'), { target: { value: user.name } })
  fireEvent.change(screen.getByLabelText('Пароль'), { target: { value: 'test-password' } })
  fireEvent.click(screen.getByLabelText('Роль'))
  fireEvent.click(screen.getByRole('option', { name: 'Администратор' }))
  fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }))
  expect(await screen.findByText('Логин занят')).toBeInTheDocument()
  fireEvent.change(screen.getByLabelText('Логин'), { target: { value: user.username } })
  fireEvent.click(screen.getByRole('button', { name: 'Создать пользователя' }))
  expect(await screen.findByText(user.username)).toBeInTheDocument()
  expect(screen.getByText('Пользователь добавлен')).toBeInTheDocument()
  expect(screen.queryByRole('heading', { name: 'Добавить пользователя' })).not.toBeInTheDocument()
  expect(JSON.parse(createUser.mock.lastCall![0].body)).toEqual({
    username: user.username, name: user.name, role: user.role, password: 'test-password',
  })
})

it('navigates from the import report and clears the session on logout', async () => {
  pathname = '/import-report'
  const logout = jest.fn().mockResolvedValue(json({ ok: true }))
  workspaceRequests('Администратор', { 'DELETE /api/auth': logout })
  render(<Workspace />)
  expect(await screen.findByRole('heading', { name: 'Отчёт об импорте' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Сформировать список на обзвон' }))
  expect(nav.push).toHaveBeenLastCalledWith('/call-list')
  fireEvent.click(screen.getByRole('button', { name: /Открыть в таблице клиентов/ }))
  expect(nav.push).toHaveBeenLastCalledWith('/')
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Выйти' })))
  expect(screen.getByRole('heading', { name: 'Контур КЦР' })).toBeInTheDocument()
  expect(sessionStorage.getItem('kontur-role')).toBeNull()
  expect(logout).toHaveBeenCalledTimes(1)
})
