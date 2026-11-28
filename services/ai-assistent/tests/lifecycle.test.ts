import { describe, expect, it } from '@jest/globals';
import type { AddressInfo } from 'node:net';
import { createApp, type App } from '../src/app.js';
import { runUntilShutdown } from '../src/lifecycle.js';
import { FakeSignals, createTestLogger, request, testConfig, type TestLogger } from './helpers.js';

function build(log: TestLogger, overrides: Parameters<typeof testConfig>[0] = {}): App {
  return createApp(testConfig(overrides), { logger: log.logger });
}

function portOf(app: App): number {
  return (app.httpAddress() as AddressInfo).port;
}

describe('runUntilShutdown', () => {
  it('starts the app, stops it on SIGTERM and resolves with 0', async () => {
    const log = createTestLogger();
    const app = build(log);
    const signals = new FakeSignals();
    const run = runUntilShutdown(app, { logger: log.logger, signals });

    await new Promise<void>((resolve) => {
      const poll = (): void => {
        if (app.httpAddress() !== null) {
          resolve();
          return;
        }
        setTimeout(poll, 5);
      };
      poll();
    });
    expect((await request(`http://127.0.0.1:${portOf(app)}/healthz`)).status).toBe(200);
    log.lines.length = 0;

    signals.fire('SIGTERM');
    await expect(run).resolves.toBe(0);
    expect(app.httpAddress()).toBeNull();
    expect(log.lines.map((line) => [line.msg, line.signal])).toEqual([
      ['received signal, shutting down', 'SIGTERM'],
      ['http server stopped', undefined],
      ['service stopped', undefined],
    ]);
    expect(signals.listenerCount('SIGTERM')).toBe(0);
    expect(signals.listenerCount('SIGINT')).toBe(0);
  });

  it('treats SIGINT the same way', async () => {
    const log = createTestLogger();
    const app = build(log);
    const signals = new FakeSignals();
    const run = runUntilShutdown(app, { logger: log.logger, signals });
    await new Promise((resolve) => {
      setTimeout(resolve, 100);
    });
    signals.fire('SIGINT');
    await expect(run).resolves.toBe(0);
  });

  it('stops listening after the first signal', async () => {
    const log = createTestLogger();
    const app = build(log);
    const signals = new FakeSignals();
    const run = runUntilShutdown(app, { logger: log.logger, signals });
    await new Promise((resolve) => {
      setTimeout(resolve, 100);
    });
    signals.fire('SIGTERM');
    await run;
    signals.fire('SIGTERM');
    expect(log.lines.filter((line) => line.msg === 'received signal, shutting down')).toHaveLength(1);
  });

  it('resolves with 1 and logs the cause when the app cannot start', async () => {
    const log = createTestLogger();
    const app: App = {
      config: testConfig(),
      logger: log.logger,
      start() {
        return Promise.reject(Object.assign(new Error('listen EADDRINUSE'), { code: 'EADDRINUSE' }));
      },
      stop: () => Promise.resolve(),
      httpAddress: () => null,
    };
    const signals = new FakeSignals();
    await expect(runUntilShutdown(app, { logger: log.logger, signals })).resolves.toBe(1);
    expect(log.lines).toEqual([
      expect.objectContaining({
        level: 'error',
        msg: 'service failed to start',
        error: expect.objectContaining({ code: 'EADDRINUSE' }),
      }),
    ]);
    expect(signals.listenerCount('SIGTERM')).toBe(0);
  });

  it('resolves with 1 when the app cannot stop', async () => {
    const log = createTestLogger();
    const app: App = {
      config: testConfig(),
      logger: log.logger,
      start: () => Promise.resolve(),
      stop: () => Promise.reject(new Error('socket hung up')),
      httpAddress: () => null,
    };
    const signals = new FakeSignals();
    const run = runUntilShutdown(app, { logger: log.logger, signals });
    signals.fire('SIGTERM');
    await expect(run).resolves.toBe(1);
    expect(log.lines.map((line) => line.msg)).toEqual([
      'received signal, shutting down',
      'service started',
      'service shutdown failed',
    ]);
  });

  it('uses the process as the signal source by default', async () => {
    const log = createTestLogger();
    let stopped = 0;
    const app: App = {
      config: testConfig(),
      logger: log.logger,
      start: () => Promise.resolve(),
      stop: () => {
        stopped += 1;
        return Promise.resolve();
      },
      httpAddress: () => null,
    };
    const before = process.listenerCount('SIGTERM');
    const run = runUntilShutdown(app, { logger: log.logger });
    expect(process.listenerCount('SIGTERM')).toBe(before + 1);
    process.emit('SIGTERM', 'SIGTERM');
    await expect(run).resolves.toBe(0);
    expect(stopped).toBe(1);
    expect(process.listenerCount('SIGTERM')).toBe(before);
  });
});
