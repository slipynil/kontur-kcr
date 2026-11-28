import { afterEach, describe, expect, it } from '@jest/globals';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import {
  INTERNAL_ERROR,
  METHOD_NOT_ALLOWED,
  NOT_FOUND,
  closeServer,
  createHttpServer,
  listen,
  sendJson,
  type Route,
} from '../src/http/server.js';
import { HEALTH_PATH, healthRoute } from '../src/http/routes.js';
import { createTestLogger, request, type TestLogger } from './helpers.js';

const servers: Server[] = [];

async function start(routes: readonly Route[], log: TestLogger): Promise<string> {
  const server = createHttpServer({ logger: log.logger, routes });
  servers.push(server);
  await listen(server, '127.0.0.1', 0);
  const { port } = server.address() as AddressInfo;
  return `http://127.0.0.1:${port}`;
}

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => closeServer(server, 1_000)));
});

const hanging: Route = {
  method: 'GET',
  path: '/hang',
  async handle(_req, res) {
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 300);
    });
    sendJson(res, 200, { done: true });
  },
};

describe('createHttpServer', () => {
  it('answers GET /healthz with {"status":"ok"}', async () => {
    const log = createTestLogger();
    const base = await start([healthRoute()], log);
    const response = await request(`${base}${HEALTH_PATH}`);
    expect(response.status).toBe(200);
    expect(response.body).toEqual({ status: 'ok' });
    expect(response.headers.get('content-type')).toBe('application/json; charset=utf-8');
    expect(response.headers.get('cache-control')).toBe('no-store');
  });

  it('answers 404 with the Error shape for an unknown path', async () => {
    const base = await start([healthRoute()], createTestLogger());
    const response = await request(`${base}/internal/v1/assistant/summary/6671-0042`);
    expect(response.status).toBe(404);
    expect(response.body).toEqual(NOT_FOUND);
  });

  it('does not treat a trailing slash or a query string as the same path', async () => {
    const base = await start([healthRoute()], createTestLogger());
    expect((await request(`${base}/healthz/`)).status).toBe(404);
    expect((await request(`${base}/healthz?probe=1`)).status).toBe(200);
  });

  it('answers 405 with Allow when the path exists for another method', async () => {
    const base = await start([healthRoute()], createTestLogger());
    const response = await request(`${base}${HEALTH_PATH}`, { method: 'POST', body: '{}' });
    expect(response.status).toBe(405);
    expect(response.body).toEqual(METHOD_NOT_ALLOWED);
    expect(response.headers.get('allow')).toBe('GET');
  });

  it('logs method, path, status and duration only', async () => {
    const log = createTestLogger();
    const base = await start([hanging], log);
    await request(`${base}/hang?search=7707083893&x=1`, { headers: { 'x-user-id': 'u1' } });
    const line = log.lines.find((entry) => entry.msg === 'http request');
    expect(line).toEqual({
      time: '2026-11-28T12:00:00.000Z',
      level: 'info',
      service: 'ai-assistent',
      msg: 'http request',
      method: 'GET',
      path: '/hang',
      status: 200,
      durationMs: expect.any(Number),
    });
    expect(JSON.stringify(line)).not.toContain('7707083893');
    expect(JSON.stringify(line)).not.toContain('x-user-id');
  });

  it('keeps health checks out of the info log', async () => {
    const log = createTestLogger('info');
    const base = await start([healthRoute()], log);
    await request(`${base}${HEALTH_PATH}`);
    expect(log.lines).toEqual([]);
  });

  it('returns 500 with the Error shape when a route throws', async () => {
    const log = createTestLogger();
    const base = await start(
      [
        {
          method: 'GET',
          path: '/boom',
          handle() {
            throw new Error('unexpected');
          },
        },
      ],
      log,
    );
    const response = await request(`${base}/boom`);
    expect(response.status).toBe(500);
    expect(response.body).toEqual(INTERNAL_ERROR);
    expect(log.lines.map((line) => line.msg)).toEqual(['http request failed', 'http request']);
    expect(log.lines[0]?.error).toMatchObject({ message: 'unexpected' });
  });

  it('aborts the response when a route throws after the headers were sent', async () => {
    const log = createTestLogger();
    const base = await start(
      [
        {
          method: 'GET',
          path: '/late-boom',
          handle(_req, res) {
            res.writeHead(200, { 'content-type': 'text/plain' });
            res.write('partial');
            throw new Error('too late');
          },
        },
      ],
      log,
    );
    await expect(request(`${base}/late-boom`)).rejects.toThrow();
    expect(log.lines[0]?.msg).toBe('http request failed');
  });

  it('passes the logger to route handlers', async () => {
    const log = createTestLogger();
    const base = await start(
      [
        {
          method: 'GET',
          path: '/logged',
          handle(_req, res, context) {
            context.logger.info('route says hello');
            sendJson(res, 200, { ok: true });
          },
        },
      ],
      log,
    );
    const response = await request(`${base}/logged`);
    expect(response.status).toBe(200);
    expect(log.lines.map((line) => line.msg)).toEqual(['route says hello', 'http request']);
  });
});

describe('listen', () => {
  it('rejects when the port is already taken', async () => {
    const log = createTestLogger();
    const server = createHttpServer({ logger: log.logger, routes: [] });
    servers.push(server);
    await listen(server, '127.0.0.1', 0);
    const { port } = server.address() as AddressInfo;
    const second: Server = createHttpServer({ logger: log.logger, routes: [] });
    servers.push(second);
    await expect(listen(second, '127.0.0.1', port)).rejects.toMatchObject({ code: 'EADDRINUSE' });
  });
});

describe('closeServer', () => {
  function portOf(server: Server): number {
    return (server.address() as AddressInfo).port;
  }

  it('lets an in-flight request finish before resolving', async () => {
    const log = createTestLogger();
    const server = createHttpServer({ logger: log.logger, routes: [hanging] });
    servers.push(server);
    await listen(server, '127.0.0.1', 0);
    const pending = request(`http://127.0.0.1:${portOf(server)}/hang`);
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    const closed = closeServer(server, 10_000);
    const response = await pending;
    expect(response.body).toEqual({ done: true });
    await closed;
    expect(server.listening).toBe(false);
  });

  it('drops a request still running at the timeout', async () => {
    const log = createTestLogger();
    const server = createHttpServer({ logger: log.logger, routes: [hanging] });
    await listen(server, '127.0.0.1', 0);
    const url = `http://127.0.0.1:${portOf(server)}/hang`;
    const pending = request(url);
    await new Promise((resolve) => {
      setTimeout(resolve, 50);
    });
    await closeServer(server, 50);
    expect(server.listening).toBe(false);
    await expect(pending).rejects.toThrow();
  });

  it('resolves for a server that never listened', async () => {
    const server = createHttpServer({ logger: createTestLogger().logger, routes: [] });
    await expect(closeServer(server, 50)).resolves.toBeUndefined();
  });
});
