/** @jest-environment node */
import { NextRequest } from 'next/server';
import { proxy, config } from '../proxy';

afterEach(() => { delete process.env.GATEWAY_INTERNAL_URL; });

it('proxies only API paths and preserves their path and query', () => {
  delete process.env.GATEWAY_INTERNAL_URL;
  const response = proxy(new NextRequest('http://localhost:3000/api/v1/clients?search=A%26B&page=2'));
  expect(config.matcher).toBe('/api/v1/:path*');
  expect(response.headers.get('x-middleware-rewrite')).toBe('http://127.0.0.1:8080/api/v1/clients?search=A%26B&page=2');
});

it('reads the internal Gateway address at runtime', () => {
  const request = new NextRequest('http://localhost:3000/api/v1/auth/login');
  process.env.GATEWAY_INTERNAL_URL = 'http://gateway:8080/';
  expect(proxy(request).headers.get('x-middleware-rewrite')).toBe('http://gateway:8080/api/v1/auth/login');
  process.env.GATEWAY_INTERNAL_URL = 'http://replacement:9000';
  expect(proxy(request).headers.get('x-middleware-rewrite')).toBe('http://replacement:9000/api/v1/auth/login');
});
