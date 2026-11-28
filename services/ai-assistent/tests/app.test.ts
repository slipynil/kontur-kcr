import { afterEach, describe, expect, it, jest } from '@jest/globals';
import type { Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { SHUTDOWN_TIMEOUT_MS, createApp, type App } from '../src/app.js';
import { closeServer, createHttpServer, listen } from '../src/http/server.js';
import { HEALTH_PATH } from '../src/http/routes.js';
import { createTestLogger, request, testConfig, type TestLogger } from './helpers.js';

const apps: App[] = [];

function build(overrides: Parameters<typeof testConfig>[0] = {}): App & { log: TestLogger } {
  const log = createTestLogger();
  const app = createApp(testConfig(overrides), { logger: log.logger });
  apps.push(app);
  return Object.assign(app, { log });
}

afterEach(async () => {
  await Promise.all(apps.splice(0).map((app) => app.stop()));
});

async function url(app: App): Promise<string> {
  const { port } = app.httpAddress() as AddressInfo;
  return `http://127.0.0.1:${port}`;
}

describe('createApp', () => {
  it('binds every interface on the configured port and answers /healthz', async () => {
    const app = build();
    expect(app.httpAddress()).toBeNull();
    await app.start();
    expect(app.httpAddress()?.address).toBe('0.0.0.0');
    const response = await request(`${await url(app)}${HEALTH_PATH}`);
    expect(response.status).toBe(200);
    expect(response.body).toEqual({ status: 'ok' });
    expect(app.log.lines).toEqual([
      expect.objectContaining({ level: 'info', msg: 'http server listening', host: '0.0.0.0' }),
      expect.objectContaining({ level: 'debug', msg: 'http request', path: HEALTH_PATH, status: 200 }),
    ]);
  });

  it('closes the port on stop and logs it', async () => {
    const app = build();
    await app.start();
    const base = await url(app);
    app.log.lines.length = 0;
    await app.stop();
    expect(app.httpAddress()).toBeNull();
    await expect(request(`${base}${HEALTH_PATH}`)).rejects.toMatchObject({
      cause: { code: 'ECONNREFUSED' },
    });
    expect(app.log.lines.map((line) => line.msg)).toEqual(['http server stopped']);
  });

  it('is idempotent to stop twice', async () => {
    const app = build();
    await app.start();
    await app.stop();
    await expect(app.stop()).resolves.toBeUndefined();
  });

  it('resolves stop for an app that never started', async () => {
    const app = build();
    await expect(app.stop()).resolves.toBeUndefined();
  });

  it('rejects start when the port is taken and still stops cleanly', async () => {
    const blocker: Server = createHttpServer({ logger: createTestLogger().logger, routes: [] });
    await listen(blocker, '0.0.0.0', 0);
    const { port } = blocker.address() as AddressInfo;
    const app = build({ port });
    try {
      await expect(app.start()).rejects.toMatchObject({ code: 'EADDRINUSE' });
    } finally {
      await closeServer(blocker, 1_000);
    }
    await expect(app.stop()).resolves.toBeUndefined();
  });

  it('honours a stop that arrives before the port is bound', async () => {
    const app = build();
    const started = app.start();
    const stopped = app.stop();
    await Promise.all([started, stopped]);
    expect(app.httpAddress()).toBeNull();
    expect(app.log.lines.map((line) => line.msg)).not.toContain('http server listening');
  });

  it('logs to stdout at the configured level when no logger is injected', async () => {
    const write = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
    const unlogged: App[] = [];
    try {
      const quiet = createApp(testConfig({ log: Object.freeze({ level: 'error' }) }));
      unlogged.push(quiet);
      await quiet.start();
      await request(`${await url(quiet)}${HEALTH_PATH}`);
      expect(write).not.toHaveBeenCalled();

      const verbose = createApp(testConfig({ log: Object.freeze({ level: 'info' }) }));
      unlogged.push(verbose);
      await verbose.start();
      const messages = write.mock.calls.map(([chunk]) => (JSON.parse(String(chunk)) as { msg: string }).msg);
      expect(messages).toEqual(['http server listening']);
    } finally {
      await Promise.all(unlogged.map((app) => app.stop()));
      write.mockRestore();
    }
  });

  it('bounds a shutdown at ten seconds', () => {
    expect(SHUTDOWN_TIMEOUT_MS).toBe(10_000);
  });
});
