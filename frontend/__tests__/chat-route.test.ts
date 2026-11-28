/** @jest-environment node */
import { NextRequest } from 'next/server';
import { POST } from '../app/api/v1/assistant/chat/[code]/route';

const chatHeaders = {
  'content-type': 'text/event-stream', 'cache-control': 'no-cache', 'x-accel-buffering': 'no',
  'x-vercel-ai-ui-message-stream': 'v1', 'content-encoding': 'gzip', 'content-length': '12',
};
const context = (code: string) => ({ params: Promise.resolve({ code }) });
const request = (headers: Record<string, string> = { authorization: 'Bearer jwt', 'content-type': 'application/json' }) =>
  new NextRequest('http://localhost:3000/api/v1/assistant/chat/A%2FB', {
    method: 'POST', headers, body: JSON.stringify({ messages: [{ id: 'm1', role: 'user', parts: [{ type: 'text', text: 'Вопрос' }] }] }),
  });
function mockFetch(status = 200, body: BodyInit | null = null, headers: HeadersInit = chatHeaders) {
  const fetchMock = jest.fn(async () => new Response(body, { status, headers }));
  global.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

afterEach(() => { delete process.env.GATEWAY_INTERNAL_URL; });

it('streams the chat answer of the Gateway to the browser as it arrives', async () => {
  process.env.GATEWAY_INTERNAL_URL = 'http://gateway:8080/';
  let release!: ReadableStreamDefaultController<Uint8Array>;
  const fetchMock = mockFetch(200, new ReadableStream<Uint8Array>({ start: controller => { release = controller; } }));

  const response = await POST(request(), context('A/B'));

  const [url, init] = fetchMock.mock.calls[0] as unknown as [URL, RequestInit];
  expect(String(url)).toBe('http://gateway:8080/api/v1/assistant/chat/A%2FB');
  expect(init).toMatchObject({
    method: 'POST', cache: 'no-store',
    body: JSON.stringify({ messages: [{ id: 'm1', role: 'user', parts: [{ type: 'text', text: 'Вопрос' }] }] }),
  });
  expect((init.headers as Headers).get('authorization')).toBe('Bearer jwt');
  expect((init.headers as Headers).get('content-type')).toBe('application/json');
  // The stream headers of the contract reach the browser; the body headers describe the Gateway body that is re-read.
  expect(response.status).toBe(200);
  expect(Object.fromEntries(response.headers)).toEqual({
    'content-type': 'text/event-stream', 'cache-control': 'no-cache', 'x-accel-buffering': 'no', 'x-vercel-ai-ui-message-stream': 'v1',
  });
  const reader = response.body!.getReader();
  const next = reader.read();
  release.enqueue(new TextEncoder().encode('data: {"type":"text-delta","delta":"Hi"}\n\n'));
  expect(new TextDecoder().decode((await next).value)).toBe('data: {"type":"text-delta","delta":"Hi"}\n\n');
  release.close();
  expect((await reader.read()).done).toBe(true);
});

it('passes the status and the explanation of a refused chat request', async () => {
  const error = { code: 'NOT_FOUND', message: 'Объект не найден' };
  mockFetch(404, JSON.stringify(error), { 'content-type': 'application/json' });

  const response = await POST(request({ 'content-type': 'application/json' }), context('A'));

  expect((global.fetch as jest.Mock).mock.calls[0][1].headers.has('authorization')).toBe(false);
  expect(response.status).toBe(404);
  expect(response.headers.get('content-type')).toBe('application/json');
  expect(await response.json()).toEqual(error);
});

it('answers with an unavailable Gateway error when the chat cannot be reached', async () => {
  global.fetch = jest.fn(async () => { throw new TypeError('fetch failed'); }) as unknown as typeof fetch;

  const response = await POST(request(), context('A'));

  expect(response.status).toBe(502);
  expect(await response.json()).toEqual({ code: 'BAD_GATEWAY', message: 'Сервис временно недоступен, повторите позже' });
});
