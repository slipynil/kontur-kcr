import type { NextRequest } from 'next/server';

// Forwards the chat answer (docs/api/public-api.yaml, assistantChat) from the Gateway event by event.
// proxy.ts leaves this path to the route: a rewrite hands the stream to Next.js compression, which releases it only when it ends.
const BODY_HEADERS = ['content-encoding', 'content-length', 'transfer-encoding'];

export async function POST(request: NextRequest, context: RouteContext<'/api/v1/assistant/chat/[code]'>) {
  const { code } = await context.params;
  const gateway = process.env.GATEWAY_INTERNAL_URL || 'http://127.0.0.1:8080';
  const destination = new URL(`/api/v1/assistant/chat/${encodeURIComponent(code)}`, gateway);
  const headers = new Headers({ 'content-type': request.headers.get('content-type') ?? 'application/json' });
  const authorization = request.headers.get('authorization');
  if (authorization) headers.set('authorization', authorization);
  try {
    const upstream = await fetch(destination, {
      method: 'POST', headers, body: await request.text(), cache: 'no-store', signal: request.signal,
    });
    // The body is re-sent as it is read, so the headers that describe the body as received no longer match.
    const forwarded = new Headers(upstream.headers);
    BODY_HEADERS.forEach(name => forwarded.delete(name));
    return new Response(upstream.body, { status: upstream.status, headers: forwarded });
  } catch {
    return Response.json({ code: 'BAD_GATEWAY', message: 'Сервис временно недоступен, повторите позже' }, { status: 502 });
  }
}
