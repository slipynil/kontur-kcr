import { act, fireEvent, render, screen } from '@testing-library/react'
import ReassignmentNotifications from '../components/reassignment-notifications'

const originalFetch = globalThis.fetch
const fetchMock = jest.fn()
const incoming = {
  id: 'incoming', code: 'AAA00412', incoming: true, status: 'PENDING',
  requester: 'Олег П.', expiresAt: '2026-11-14T10:00:00Z',
}
const outgoing = { ...incoming, id: 'outgoing', code: 'AAA02210', incoming: false, status: 'TRANSFERRED' }
const resolved = { ...incoming, id: 'resolved', code: 'AAA04012', status: 'REJECTED' }
const reply = (data: unknown, ok = true) => ({ ok, json: async () => data })

beforeEach(() => {
  fetchMock.mockReset()
  globalThis.fetch = fetchMock
})
afterEach(() => {
  globalThis.fetch = originalFetch
  jest.useRealTimers()
})

it('shows pending incoming and completed outgoing requests and refreshes after a refusal', async () => {
  fetchMock.mockResolvedValueOnce(reply({ items: [incoming, outgoing, resolved] }))
    .mockResolvedValueOnce(reply({ status: 'REJECTED' }))
    .mockResolvedValueOnce(reply({ items: [{ ...incoming, status: 'REJECTED' }, outgoing] }))
  render(<ReassignmentNotifications />)
  const link = await screen.findByRole('link', { name: incoming.code })
  expect(link).toHaveAttribute('href', '/client/' + incoming.code)
  expect(screen.getByText(/Олег П. запросил перезакрепление.*13:00 мск/)).toBeInTheDocument()
  expect(screen.getByText('Клиент перезакреплён')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: resolved.code })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Отказать' }))
  expect(screen.getByRole('button', { name: 'Отказать' })).toBeDisabled()
  expect(await screen.findByRole('status')).toHaveTextContent('Запрос отклонён. Клиент остаётся за вами.')
  expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/operator-work', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: incoming.id }),
  })
  expect(screen.queryByRole('link', { name: incoming.code })).not.toBeInTheDocument()
})

it('reports rejection conflicts and network errors while keeping the request available', async () => {
  const items = [incoming]
  fetchMock.mockResolvedValueOnce(reply({ items }))
    .mockResolvedValueOnce(reply({ message: 'Запрос уже обработан' }, false))
    .mockResolvedValueOnce(reply({ items }))
    .mockRejectedValueOnce(new Error('offline'))
  render(<ReassignmentNotifications />)
  const reject = await screen.findByRole('button', { name: 'Отказать' })
  fireEvent.click(reject)
  expect(await screen.findByText('Запрос уже обработан')).toBeInTheDocument()
  expect(reject).toBeEnabled()
  fireEvent.click(reject)
  expect(await screen.findByText('Не удалось обновить запрос. Проверьте его статус.')).toBeInTheDocument()
  expect(reject).toBeEnabled()
})

it('recovers on the next poll and cancels polling on unmount', async () => {
  jest.useFakeTimers()
  fetchMock.mockResolvedValueOnce(reply({}, false))
    .mockResolvedValueOnce(reply({ items: [outgoing] }))
  const { unmount } = render(<ReassignmentNotifications />)
  await act(async () => {})
  expect(screen.queryByRole('region')).not.toBeInTheDocument()
  await act(async () => { jest.advanceTimersByTime(5000) })
  expect(screen.getByText('Клиент перезакреплён')).toBeInTheDocument()
  const signal = fetchMock.mock.lastCall![1].signal as AbortSignal
  unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { jest.advanceTimersByTime(5000) })
  expect(fetchMock).toHaveBeenCalledTimes(2)
})
