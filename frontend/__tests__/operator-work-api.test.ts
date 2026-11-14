/** @jest-environment node */
import { cookies } from 'next/headers'
import { GET as readClient, DELETE as releaseClient } from '../app/api/clients/[code]/route'
import { POST as claimClient } from '../app/api/clients/[code]/claim/route'
import { GET as readEvents, POST as saveEvent } from '../app/api/clients/[code]/events/route'
import { GET as readWork, PATCH as reject, POST as publish } from '../app/api/operator-work/route'
import { sessions } from '../lib/backend'
import { actorId } from '../lib/operator-work'

jest.mock('next/headers', () => ({ cookies: jest.fn() }))

const operator = { username: 'operator', name: 'Анна К.', role: 'Оператор' } as const
const admin = { username: 'vera.v', name: 'Вера В.', role: 'Администратор' } as const
const code = 'AAA00412'
const quarter = '2026_Q4'
const serviceUrl = 'http://operator-work.test'
const notificationId = 'ca10b233-6a97-42ef-a169-f4e6018c130d'
const context = (client = code) => ({ params: Promise.resolve({ code: client }) })
const get = (query = '') => new Request('http://localhost/' + query)
const body = (payload: unknown, method = 'POST') =>
  new Request('http://localhost', { method, body: JSON.stringify(payload) })

let upstream: jest.SpiedFunction<typeof fetch>
let session: string | undefined

beforeEach(() => {
  session = 'operator'
  sessions.set('operator', operator)
  sessions.set('admin', admin)
  jest.mocked(cookies).mockImplementation(async () =>
    ({ get: () => session ? { value: session } : undefined }) as unknown as Awaited<ReturnType<typeof cookies>>,
  )
  jest.replaceProperty(process, 'env', {
    ...process.env,
    OPERATOR_WORK_URL: serviceUrl,
    OPERATOR_WORK_SERVICE_TOKEN: 'test-token',
    OPERATOR_WORK_QUARTER: quarter,
  })
  upstream = jest.spyOn(globalThis, 'fetch')
})

afterEach(() => {
  sessions.clear()
  jest.restoreAllMocks()
})

function expectUpstream(path: string, method: string, actor: typeof operator | typeof admin, payload?: unknown) {
  const [url, init] = upstream.mock.lastCall!
  expect(String(url)).toBe(serviceUrl + '/internal/v1' + path)
  expect(init).toEqual(expect.objectContaining({
    method,
    headers: expect.objectContaining({
      Authorization: 'Bearer test-token',
      'X-User-Id': actorId(actor.username),
      'X-User-Role': actor === operator ? 'OPERATOR' : 'ADMIN',
    }),
    body: payload === undefined ? undefined : JSON.stringify(payload),
    cache: 'no-store',
  }))
}

it('loads assignment details and distinguishes unpublished clients from service errors', async () => {
  const assignment = {
    assigned_operator_id: actorId(operator.username), assignment_id: 'assignment',
    work_quarter: quarter, stage: 'WAIT_DEADLINE', wait_until: '2026-11-25', awaiting_balance: false,
  }
  upstream.mockResolvedValueOnce(Response.json(assignment))
    .mockResolvedValueOnce(new Response(null, { status: 404 }))
  const failure = Response.json({ message: 'Недоступно' }, { status: 503 })
  upstream.mockResolvedValueOnce(failure)

  expect(await (await readClient(get(), context())).json()).toEqual({
    code, owner: operator.name, operatorWork: true, published: true, ownerIsMe: true,
    assignmentId: 'assignment', workQuarter: quarter, stage: 'WAIT_DEADLINE',
    waitUntil: '2026-11-25', awaitingBalance: false,
  })
  expectUpstream('/clients/' + code + '/assignment?work_quarter=' + quarter, 'GET', operator)
  expect(await (await readClient(get(), context())).json()).toEqual({
    code, owner: null, operatorWork: true, published: false,
  })
  expect(await readClient(get(), context())).toBe(failure)
  session = 'admin'
  expect((await releaseClient(get(), context())).status).toBe(405)
  expect(upstream).toHaveBeenCalledTimes(3)
})

it('forwards a take request with its original assignment and request IDs', async () => {
  const payload = { expected_assignment_id: 'assignment', request_id: 'request' }
  const pending = Response.json({ status: 'PENDING' }, { status: 202 })
  upstream.mockResolvedValueOnce(pending)
  expect((await claimClient(body(payload), context('missing'))).status).toBe(404)
  expect(await claimClient(body(payload), context())).toBe(pending)
  expectUpstream('/clients/' + code + '/take?work_quarter=' + quarter, 'POST', operator, payload)
})

it('loads the requested history page and resolves event authors', async () => {
  const items = [
    { author_id: actorId(operator.username), id: 'call' },
    { author_id: '00000000-0000-0000-0000-000000000000', id: 'expiry' },
    { author_id: 'unknown-author', id: 'comment' },
  ]
  upstream.mockResolvedValueOnce(Response.json({ items, total: 3, page_size: 2 }))
  expect(await (await readEvents(get('?page=2'), context())).json()).toEqual({
    total: 3, page_size: 2,
    items: items.map((item, index) => ({ ...item, author_name: [operator.name, 'Система', 'unknown-author'][index] })),
  })
  expectUpstream('/clients/' + code + '/events?page=2', 'GET', operator)

  const failure = Response.json({ message: 'Недоступно' }, { status: 503 })
  upstream.mockResolvedValueOnce(failure)
  expect(await readEvents(get(), context())).toBe(failure)
  expectUpstream('/clients/' + code + '/events?page=1', 'GET', operator)
})

it('checks event access and sends historical events unchanged', async () => {
  const payload = { event_type: 'HISTORICAL_COMMENT', request_id: 'request', comment_text: 'Запись' }
  session = undefined
  expect((await readEvents(get(), context())).status).toBe(401)
  expect((await saveEvent(body(payload), context())).status).toBe(401)
  session = 'admin'
  expect((await saveEvent(body(payload), context())).status).toBe(403)
  session = 'operator'
  expect((await readEvents(get(), context('missing'))).status).toBe(404)
  expect((await saveEvent(body(payload), context('missing'))).status).toBe(404)
  expect(upstream).not.toHaveBeenCalled()

  const saved = Response.json({ id: 'event' }, { status: 201 })
  upstream.mockResolvedValueOnce(saved)
  expect(await saveEvent(body(payload), context())).toBe(saved)
  expectUpstream('/clients/' + code + '/events', 'POST', operator, payload)
})

it('restricts pool reads to operators and maps published entries', async () => {
  session = undefined
  expect((await readWork(get('?pool'))).status).toBe(401)
  session = 'admin'
  expect(await (await readWork(get('?pool'))).json()).toEqual({ items: [] })
  expect(upstream).not.toHaveBeenCalled()
  session = 'operator'
  upstream.mockResolvedValueOnce(Response.json({ items: [
    { kcr_code: code, assigned_operator_id: null },
    { kcr_code: 'AAA02210', assigned_operator_id: actorId(operator.username) },
  ] }))
  expect(await (await readWork(get('?pool'))).json()).toEqual({
    enabled: true, items: [{ code, owner: 'Свободен' }, { code: 'AAA02210', owner: operator.name }],
  })
  expectUpstream('/pool?work_quarter=' + quarter, 'GET', operator)
  const failure = new Response(null, { status: 503 })
  upstream.mockResolvedValueOnce(failure)
  expect(await readWork(get('?pool'))).toBe(failure)
})

it('maps incoming and outgoing notifications and forwards a valid rejection', async () => {
  const expires = '2026-11-14T10:00:00Z'
  upstream.mockResolvedValueOnce(Response.json({ items: [
    { id: notificationId, owner_id: actorId(operator.username), requester_id: 'peer', kcr_code: code, expires_at: expires, status: 'PENDING' },
    { id: 'outgoing', owner_id: 'peer', requester_id: actorId(operator.username), kcr_code: code, expires_at: expires, status: 'REJECTED' },
  ] }))
  expect(await (await readWork(get())).json()).toEqual({ items: [
    { id: notificationId, code, expiresAt: expires, status: 'PENDING', incoming: true, requester: 'peer' },
    { id: 'outgoing', code, expiresAt: expires, status: 'REJECTED', incoming: false, requester: operator.name },
  ] })
  expectUpstream('/reassignment-notifications', 'GET', operator)
  session = 'admin'
  expect((await reject(body({ id: notificationId }, 'PATCH'))).status).toBe(403)
  session = 'operator'
  expect((await reject(body({ id: 'invalid' }, 'PATCH'))).status).toBe(400)
  upstream.mockResolvedValueOnce(Response.json({ status: 'REJECTED' }))
  expect(await (await reject(body({ id: notificationId }, 'PATCH'))).json()).toEqual({ status: 'REJECTED' })
  expectUpstream('/reassignment-notifications/' + notificationId + '/reject', 'POST', operator)
})

it('validates an entire publication and forwards it as one administrative command', async () => {
  const payload = { codes: [code, 'AAA02210'] }
  expect((await publish(body(payload))).status).toBe(403)
  session = 'admin'
  for (const codes of [null, [], [code, 'unknown'], [code, 23]]) {
    expect((await publish(body({ codes }))).status).toBe(400)
  }
  expect(upstream).not.toHaveBeenCalled()
  upstream.mockResolvedValueOnce(new Response(null, { status: 204 }))
  expect(await (await publish(body(payload))).json()).toEqual({ ok: true })
  expectUpstream('/pool/publication?work_quarter=' + quarter, 'PUT', admin, payload)
  const conflict = Response.json({ message: 'Конфликт' }, { status: 409 })
  upstream.mockResolvedValueOnce(conflict)
  expect(await publish(body(payload))).toBe(conflict)
})

it('reports missing configuration and a failed upstream connection', async () => {
  delete process.env.OPERATOR_WORK_QUARTER
  expect((await readWork(get('?pool'))).status).toBe(503)
  expect(upstream).not.toHaveBeenCalled()
  upstream.mockRejectedValueOnce(new Error('connection refused'))
  const failure = await readWork(get())
  expect(failure.status).toBe(503)
  expect(await failure.json()).toEqual({ message: 'Operator Work временно недоступен' })
  delete process.env.OPERATOR_WORK_URL
  expect(await (await readWork(get())).json()).toEqual({ items: [] })
})
