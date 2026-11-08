import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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
