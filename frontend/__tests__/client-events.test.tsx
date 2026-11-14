import { act, fireEvent, render, screen } from '@testing-library/react'
import { CallForm, ClientHistory } from '../components/client-events'

const code = 'AAA00412'
const path = '/api/clients/' + code + '/events'
const assignment = { assignmentId: 'original-assignment', workQuarter: '2026_Q4', ownerIsMe: true }
const fallbackError = 'Не удалось подтвердить сохранение. Повторите отправку с теми же данными.'
const response = (data: unknown, ok = true) => ({ ok, json: async () => data })
const change = (label: string, value: string) =>
  fireEvent.change(screen.getByLabelText(label), { target: { value } })

const originalFetch = globalThis.fetch
const fetchMock = jest.fn()
beforeEach(() => {
  fetchMock.mockReset()
  globalThis.fetch = fetchMock
})
afterEach(() => { globalThis.fetch = originalFetch })

function sentEvent() {
  const [url, init] = fetchMock.mock.lastCall!
  expect(url).toBe(path)
  expect(init?.method).toBe('POST')
  return JSON.parse(String(init?.body))
}

it('requires an owned assignment before opening the call form', () => {
  const onSaved = jest.fn()
  const { rerender } = render(<CallForm code={code} assignment={null} onSaved={onSaved} />)
  expect(screen.getByText('Загружается назначение клиента…')).toBeInTheDocument()
  rerender(<CallForm code={code} assignment={{ ...assignment, ownerIsMe: false }} onSaved={onSaved} />)
  expect(screen.getByText(/Возьмите клиента в работу/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Сохранить звонок' })).not.toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()
})

it('saves a completed call after reassignment with the original assignment and document quarters', async () => {
  fetchMock.mockResolvedValue(response({ wait_applied: false }) as Response)
  const onSaved = jest.fn()
  const { rerender } = render(<CallForm code={code} assignment={assignment} onSaved={onSaved} />)
  change('Комментарий', 'Договорились о передаче документов')
  change('Обещанная дата передачи', '2026-11-19')
  change('Ожидание до', '2026-11-23')
  fireEvent.change(screen.getByPlaceholderText('2026_Q1, 2026_Q2'), { target: { value: '2026_Q1,  2026_Q2' } })
  rerender(<CallForm code={code} assignment={{
    ...assignment, assignmentId: 'new-assignment', ownerIsMe: false,
  }} onSaved={onSaved} />)
  expect(screen.getByRole('status')).toHaveTextContent('Назначение изменилось')
  expect(screen.getByLabelText('Ожидание до')).toHaveAttribute('readonly')
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Сохранить звонок' })))
  expect(sentEvent()).toEqual({
    event_type: 'CALL_RESULT', expected_assignment_id: assignment.assignmentId,
    work_quarter: assignment.workQuarter, call_result: 'AGREEMENT', comment_text: 'Договорились о передаче документов',
    document_quarters: ['2026_Q1', '2026_Q2'], promised_date: '2026-11-19',
    wait_until: '2026-11-23', request_id: expect.any(String),
  })
  expect(onSaved).toHaveBeenCalledWith('Звонок сохранён в истории. Срок ожидания остался без изменений.')
})

it('reuses the call request ID after a network error and saves the wait after retry', async () => {
  fetchMock.mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce(response({ wait_applied: true }) as Response)
  const onSaved = jest.fn()
  render(<CallForm code={code} assignment={assignment} onSaved={onSaved} />)
  change('Ожидание до', '2026-11-23')
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить звонок' }))
  expect(screen.getByRole('button', { name: 'Сохраняем…' })).toBeDisabled()
  expect(await screen.findByRole('alert')).toHaveTextContent(fallbackError)
  const first = sentEvent()
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Сохранить звонок' })))
  expect(sentEvent()).toEqual(first)
  expect(onSaved).toHaveBeenCalledWith('Звонок и срок ожидания сохранены')
})

it('shows a call validation error and generates a new request ID after the result changes', async () => {
  fetchMock.mockResolvedValueOnce(response({}, false) as Response)
    .mockResolvedValueOnce(response({ wait_applied: false }) as Response)
  const onSaved = jest.fn()
  render(<CallForm code={code} assignment={assignment} onSaved={onSaved} />)
  fireEvent.click(screen.getByRole('button', { name: 'Сохранить звонок' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось сохранить звонок')
  const first = sentEvent()
  fireEvent.click(screen.getByLabelText('Недозвон'))
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Сохранить звонок' })))
  expect(sentEvent()).toMatchObject({
    request_id: expect.not.stringMatching(first.request_id), call_result: 'NO_ANSWER',
    document_quarters: [], promised_date: null, wait_until: null,
  })
  expect(onSaved).toHaveBeenCalledWith('Звонок сохранён')
})

const created = '2026-11-14T07:30:00Z'
const call = {
  id: 'call', created_at: created, author_name: 'Анна К.', call_result: 'AGREEMENT',
  comment_text: 'Перезвонить через неделю', document_quarters: ['2026_Q3'],
  promised_date: '2026-11-21', wait_until: '2026-11-24',
}
const comment = {
  id: 'comment', created_at: created, author_name: 'Олег П.', comment_text: 'Уточнили контакт',
  historical_event_date: '2026-11-12T13:15:00+03:00',
}
const history = (items: unknown[], total = items.length, pageSize = 1) =>
  response({ items, total, page_size: pageSize }) as Response

it('renders call details and navigates between event pages with Moscow timestamps', async () => {
  fetchMock.mockResolvedValueOnce(history([call], 2))
    .mockResolvedValueOnce(history([comment], 2)).mockResolvedValueOnce(history([call], 2))
  render(<ClientHistory code={code} />)
  expect(screen.getByText('Загрузка истории…')).toBeInTheDocument()
  expect(await screen.findByText('Перезвонить через неделю')).toBeInTheDocument()
  expect(screen.getByText('Дозвонились, есть договорённость')).toBeInTheDocument()
  expect(screen.getByText('Документы за 2026_Q3')).toBeInTheDocument()
  expect(screen.getByText('Обещанная дата передачи 2026-11-21')).toBeInTheDocument()
  expect(screen.getByText('Ожидание до 2026-11-24')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Предыдущая' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Следующая' }))
  expect(await screen.findByText('Прошлый комментарий')).toBeInTheDocument()
  expect(screen.getByText(/Событие .*13:15 мск/)).toBeInTheDocument()
  expect(screen.getByText(/Внесено .*10:30 мск · Олег П./)).toBeInTheDocument()
  expect(fetchMock).toHaveBeenLastCalledWith(path + '?page=2', { signal: expect.any(AbortSignal) })
  expect(screen.getByRole('button', { name: 'Следующая' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Предыдущая' }))
  expect(await screen.findByText('Перезвонить через неделю')).toBeInTheDocument()
  expect(fetchMock).toHaveBeenLastCalledWith(path + '?page=1', { signal: expect.any(AbortSignal) })
})

it('retries a failed history load and shows the empty state', async () => {
  fetchMock.mockResolvedValueOnce(response({}, false) as Response).mockResolvedValueOnce(history([]))
  render(<ClientHistory code={code} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось загрузить историю.')
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  expect(await screen.findByText('Событий пока нет.')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})

it('validates, retries and saves a historical comment, then refreshes history', async () => {
  fetchMock.mockResolvedValueOnce(history([]))
    .mockRejectedValueOnce(new Error('offline'))
    .mockResolvedValueOnce(response({}, false) as Response)
    .mockResolvedValueOnce(response({ id: comment.id }) as Response)
    .mockResolvedValueOnce(history([comment]))
  render(<ClientHistory code={code} allowComments />)
  await screen.findByText('Событий пока нет.')
  fireEvent.click(screen.getByText('Добавить прошлый комментарий'))
  const save = () => fireEvent.click(screen.getByRole('button', { name: 'Сохранить комментарий' }))
  change('Дата и время события (МСК)', '2026-11-12T13:15')
  change('Текст комментария', '   ')
  save()
  expect(screen.getByRole('alert')).toHaveTextContent('Укажите дату события и текст комментария.')
  expect(fetchMock).toHaveBeenCalledTimes(1)

  change('Текст комментария', '  Уточнили контакт  ')
  save()
  expect(await screen.findByRole('alert')).toHaveTextContent(fallbackError)
  const first = sentEvent()
  expect(first).toEqual({
    event_type: 'HISTORICAL_COMMENT', comment_text: comment.comment_text,
    historical_event_date: comment.historical_event_date, document_quarters: [],
    request_id: expect.any(String),
  })
  save()
  expect(await screen.findByRole('alert')).toHaveTextContent('Не удалось сохранить комментарий')
  expect(sentEvent()).toEqual(first)
  save()
  expect(await screen.findByRole('status')).toHaveTextContent('Комментарий сохранён')
  expect(await screen.findByText('Уточнили контакт')).toBeInTheDocument()
  expect(screen.getByLabelText('Текст комментария')).toHaveValue('')
  expect(fetchMock).toHaveBeenLastCalledWith(path + '?page=1', { signal: expect.any(AbortSignal) })
})
