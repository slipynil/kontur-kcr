import { describe, expect, it, jest } from '@jest/globals';
import { ConfigError } from '../src/config.js';
import { createLogger } from '../src/log.js';
import { createTestLogger, type LogLine } from './helpers.js';

const TIME = '2026-11-28T12:00:00.000Z';

describe('logger', () => {
  it('writes one JSON line per call with time, level, service and message', () => {
    const { logger, lines } = createTestLogger();
    logger.info('service started', { port: 8000 });
    expect(lines).toEqual([
      { time: TIME, level: 'info', service: 'ai-assistent', msg: 'service started', port: 8000 },
    ]);
  });

  it('keeps the line parseable when no fields are given', () => {
    const { logger, lines } = createTestLogger();
    logger.warn('nothing to add');
    expect(lines).toEqual([
      { time: TIME, level: 'warn', service: 'ai-assistent', msg: 'nothing to add' },
    ]);
  });

  it('drops lines below the configured level', () => {
    const { logger, lines } = createTestLogger('warn');
    logger.debug('hidden');
    logger.info('hidden');
    logger.warn('shown');
    logger.error('shown');
    expect(lines.map((line) => line.level)).toEqual(['warn', 'error']);
  });

  it('adds fields of a child logger and lets it shadow the parent', () => {
    const { logger, lines } = createTestLogger();
    const child = logger.child({ component: 'http', port: 8000 }).child({ port: 8001, request: 'r1' });
    child.info('hello');
    expect(lines).toEqual([
      {
        time: TIME,
        level: 'info',
        service: 'ai-assistent',
        msg: 'hello',
        component: 'http',
        port: 8001,
        request: 'r1',
      },
    ]);
    expect(logger.fields).toEqual({});
    expect(child.fields).toEqual({ component: 'http', port: 8001, request: 'r1' });
  });

  it('turns errors into plain objects without losing their code and cause', () => {
    const { logger, lines } = createTestLogger();
    const error = new Error('connection refused') as Error & { code?: string };
    error.code = 'ECONNREFUSED';
    error.cause = new TypeError('bad host');
    logger.error('upstream call failed', { error });
    expect(lines[0]?.error).toEqual({
      name: 'Error',
      message: 'connection refused',
      code: 'ECONNREFUSED',
      cause: { name: 'TypeError', message: 'bad host' },
    });
  });

  it('serializes dates, URLs, bigints, arrays and nested objects', () => {
    const { logger, lines } = createTestLogger();
    logger.info('kinds', {
      at: new Date(TIME),
      target: new URL('http://clients:8000/internal/v1'),
      count: 10n,
      list: [1, { deep: true }],
    });
    expect(lines[0]).toMatchObject({
      at: TIME,
      target: 'http://clients:8000/internal/v1',
      count: '10',
      list: [1, { deep: true }],
    });
  });

  it('drops undefined and functions and marks circular structures', () => {
    const { logger, lines } = createTestLogger();
    const loop: Record<string, unknown> = {};
    loop.self = loop;
    logger.info('odd fields', { missing: undefined, run: () => true, loop });
    expect(lines[0]).toEqual({
      time: TIME,
      level: 'info',
      service: 'ai-assistent',
      msg: 'odd fields',
      loop: { self: '[Circular]' },
    });
  });

  it('still emits the message when a field cannot be read', () => {
    const { logger, lines } = createTestLogger();
    const poisoned = Object.defineProperty({}, 'secret', {
      enumerable: true,
      get() {
        throw new Error('unreadable');
      },
    });
    expect(() => logger.info('kept', { poisoned })).not.toThrow();
    expect(lines).toEqual([
      { time: TIME, level: 'info', service: 'ai-assistent', msg: 'kept', fields: null },
    ]);
  });

  it('writes to stdout by default', () => {
    const write = jest.spyOn(process.stdout, 'write').mockImplementation(() => true);
    try {
      createLogger({ level: 'error' }).error('to stdout', { code: 'X' });
    } finally {
      write.mockRestore();
    }
    const written = write.mock.calls.map(([chunk]) => String(chunk));
    expect(written).toHaveLength(1);
    const line = JSON.parse(written[0] ?? '') as LogLine;
    expect(line).toMatchObject({ level: 'error', msg: 'to stdout', code: 'X' });
    expect(written[0]?.endsWith('\n')).toBe(true);
  });

  it('reports a subclassed error under its own name', () => {
    const { logger, lines } = createTestLogger();
    logger.error('startup failed', { error: new ConfigError([{ variable: 'ASSISTANT_PORT', message: 'too big' }]) });
    expect(lines[0]?.error).toEqual({
      name: 'ConfigError',
      message: expect.stringContaining('ASSISTANT_PORT'),
    });
  });
});
