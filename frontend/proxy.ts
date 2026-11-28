import { NextRequest, NextResponse } from 'next/server';

// The chat answers with a stream that app/api/v1/assistant/chat/[code]/route.ts forwards event by event.
const CHAT_PATH = /^\/api\/v1\/assistant\/chat\/[^/]+$/;

export function proxy(request: NextRequest) {
  const gateway = process.env.GATEWAY_INTERNAL_URL || 'http://127.0.0.1:8080';
  const destination = new URL(request.nextUrl.pathname + request.nextUrl.search, gateway);
  if (CHAT_PATH.test(request.nextUrl.pathname)) return NextResponse.next();
  return NextResponse.rewrite(destination);
}

export const config = {
  matcher: '/api/v1/:path*',
};
