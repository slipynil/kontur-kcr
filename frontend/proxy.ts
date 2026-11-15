import { NextRequest, NextResponse } from 'next/server';

export function proxy(request: NextRequest) {
  const gateway = process.env.GATEWAY_INTERNAL_URL || 'http://127.0.0.1:8080';
  const destination = new URL(request.nextUrl.pathname + request.nextUrl.search, gateway);
  return NextResponse.rewrite(destination);
}

export const config = {
  matcher: '/api/v1/:path*',
};
