import { EventEmitter } from 'node:events';
import type { Config } from '../src/config.js';
import type { SignalSource } from '../src/lifecycle.js';
import { createLogger, type Logger, type LoggerSink, type LogLevel } from '../src/log.js';

export interface LogLine {
  readonly level: string;
  readonly msg: string;
  readonly [key: string]: unknown;
}

export interface TestLogger {
  logger: Logger;
  lines: LogLine[];
}

/** Logger with an in-memory sink and a fixed clock, so lines are easy to assert. */
export function createTestLogger(level: LogLevel = 'debug'): TestLogger {
  const lines: LogLine[] = [];
  const sink: LoggerSink = {
    write: (line) => {
      lines.push(JSON.parse(line) as LogLine);
    },
  };
  return {
    lines,
    logger: createLogger({ level, sink, now: () => new Date('2026-11-28T12:00:00.000Z') }),
  };
}

/** Stands in for the process in shutdown tests. */
export class FakeSignals extends EventEmitter implements SignalSource {
  fire(signal: NodeJS.Signals): void {
    this.emit(signal, signal);
  }
}

export interface HttpResponse {
  status: number;
  headers: Headers;
  text: string;
  body: unknown;
}

export async function request(url: string, init: RequestInit = {}): Promise<HttpResponse> {
  const response = await fetch(url, { ...init, signal: AbortSignal.timeout(5_000) });
  const text = await response.text();
  return {
    status: response.status,
    headers: response.headers,
    text,
    body: text === '' ? null : JSON.parse(text),
  };
}

/** Minimal valid configuration for tests: ephemeral ports on both listeners. */
export function testConfig(overrides: Partial<Config> = {}): Config {
  return Object.freeze({
    port: 0,
    mcpPort: 0,
    log: Object.freeze({ level: 'info' as const }),
    ...overrides,
  });
}
