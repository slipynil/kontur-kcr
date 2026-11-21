/** @jest-environment node */
import { api, ApiError, queryString, request } from '../lib/api';
beforeEach(() => { global.fetch = jest.fn(); });
it('uses a relative API path when no public origin is configured', async () => {
  const previous = process.env.NEXT_PUBLIC_API_URL;
  delete process.env.NEXT_PUBLIC_API_URL;
  try {
    let client: typeof import('../lib/api');
    jest.isolateModules(() => { client = jest.requireActual('../lib/api'); });
    (fetch as jest.Mock).mockResolvedValue(new Response('{}'));
    await client!.api('/auth/login', { method: 'POST', body: '{}' });
    expect((fetch as jest.Mock).mock.calls[0][0]).toBe('/api/v1/auth/login');
  } finally { process.env.NEXT_PUBLIC_API_URL = previous; }
});
it('sends JSON to the public Gateway path', async () => {
  (fetch as jest.Mock).mockResolvedValue(new Response(JSON.stringify({ id: 'u1' }), { status: 201 }));
  expect(await request('/users', { method: 'POST', body: JSON.stringify({ login: 'user' }) })).toEqual({ id: 'u1' });
  const [url, init] = (fetch as jest.Mock).mock.calls[0];
  expect(url).toBe('/api/v1/users'); expect(init.headers.get('Content-Type')).toBe('application/json');
});
it('lets the browser generate the multipart boundary', async () => {
  (fetch as jest.Mock).mockResolvedValue(new Response('{}'));
  const body = new FormData(); body.set('selected_quarter', '2026_Q4');
  await api('/imports', { method: 'POST', body });
  expect((fetch as jest.Mock).mock.calls[0][1].headers.has('Content-Type')).toBe(false);
});
it('preserves machine codes and server messages for conflicts', async () => {
  (fetch as jest.Mock).mockResolvedValue(new Response(JSON.stringify({ code: 'ASSIGNMENT_CHANGED', message: 'Ответственный изменился. Обновите карточку' }), { status: 409 }));
  await expect(request('/clients/AAA/take', { method: 'POST' })).rejects.toMatchObject({ status: 409, code: 'ASSIGNMENT_CHANGED', message: 'Ответственный изменился. Обновите карточку' });
});
it('handles empty delete responses and non-JSON gateway errors', async () => {
  (fetch as jest.Mock).mockResolvedValueOnce(new Response(null, { status: 204 })).mockResolvedValueOnce(new Response('<html>unavailable</html>', { status: 502 }));
  await expect(request('/clients/A', { method: 'DELETE' })).resolves.toBeUndefined();
  await expect(request('/clients')).rejects.toBeInstanceOf(ApiError);
});
it.each(['/clients/A', '/clients/A/snapshots/2026_Q4'])('accepts a bodyless 200 from DELETE %s', async path => {
  (fetch as jest.Mock).mockResolvedValue(new Response(null, { status: 200 }));
  await expect(request<void>(path, { method: 'DELETE' })).resolves.toBeUndefined();
});
it('still reports a failed DELETE using the public error response', async () => {
  (fetch as jest.Mock).mockResolvedValue(new Response(JSON.stringify({ code: 'INVALID_STATE', message: 'Удаление недоступно' }), { status: 409 }));
  await expect(request('/clients/A', { method: 'DELETE' })).rejects.toMatchObject({ status: 409, code: 'INVALID_STATE', message: 'Удаление недоступно' });
});
it('rejects malformed success responses', async () => {
  (fetch as jest.Mock).mockResolvedValue(new Response('not JSON'));
  await expect(request('/clients')).rejects.toThrow('некорректный ответ');
});
it('serializes zero, literal search characters and comma separated tags', () => {
  const query = new URLSearchParams(queryString({ search: 'A%_ &', tags: 'VIP,MPP', min_unsubmitted_documents: 0, quarter: '' }));
  expect(query.get('search')).toBe('A%_ &'); expect(query.get('tags')).toBe('VIP,MPP'); expect(query.get('min_unsubmitted_documents')).toBe('0'); expect(query.has('quarter')).toBe(false);
});
