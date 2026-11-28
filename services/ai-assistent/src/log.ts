/**
 * JSON-line structured logging to a sink (stdout by default).
 *
 * Lines carry no personal data by construction: callers pass explicit fields,
 * and this module never reads request bodies, headers or environment.
 */

export type LogLevel = (typeof LOG_LEVELS)[number];
export type LogFields = Record<string, unknown>;

const LEVEL_PRIORITY: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };

export const LOG_LEVELS = ['debug', 'info', 'warn', 'error'] as const;

export interface Logger {
  /** Fields added to every line of this logger. */
  readonly fields: Readonly<LogFields>;
  child(fields: LogFields): Logger;
  debug(message: string, fields?: LogFields): void;
  info(message: string, fields?: LogFields): void;
  warn(message: string, fields?: LogFields): void;
  error(message: string, fields?: LogFields): void;
}

export interface LoggerSink {
  write(line: string): void;
}

export interface LoggerOptions {
  level: LogLevel;
  sink?: LoggerSink;
  /** Bound fields of the root logger; nested loggers extend them. */
  fields?: LogFields;
  now?: () => Date;
  service?: string;
}

interface SerializationContext {
  seen: Set<object>;
}

/** Makes an arbitrary value safe for JSON: errors become plain objects, cycles are marked. */
function toLogValue(value: unknown, context: SerializationContext): unknown {
  if (value === null || typeof value !== 'object') {
    return typeof value === 'bigint' ? value.toString() : value;
  }
  if (value instanceof Error) {
    const record: Record<string, unknown> = { name: value.name, message: value.message };
    const code = (value as Error & { code?: unknown }).code;
    if (code !== undefined) {
      record.code = toLogValue(code, context);
    }
    if ('cause' in value && value.cause !== undefined) {
      record.cause = toLogValue(value.cause, context);
    }
    return record;
  }
  if (value instanceof Date) {
    return value.toISOString();
  }
  if (value instanceof URL) {
    return value.href;
  }
  if (context.seen.has(value)) {
    return '[Circular]';
  }
  context.seen.add(value);
  let result: unknown;
  if (Array.isArray(value)) {
    result = value.map((item: unknown) => toLogValue(item, context));
  } else {
    const record: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value as Record<string, unknown>)) {
      if (item === undefined || typeof item === 'function' || typeof item === 'symbol') {
        continue;
      }
      record[key] = toLogValue(item, context);
    }
    result = record;
  }
  context.seen.delete(value);
  return result;
}

function serializeFields(fields: LogFields): Record<string, unknown> {
  const context: SerializationContext = { seen: new Set() };
  const record: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(fields)) {
    record[key] = toLogValue(value, context);
  }
  return record;
}

class JsonLineLogger implements Logger {
  readonly fields: Readonly<LogFields>;

  private readonly level: LogLevel;

  private readonly minPriority: number;

  private readonly sink: LoggerSink;

  private readonly now: () => Date;

  private readonly service: string;

  constructor(options: LoggerOptions) {
    this.level = options.level;
    this.minPriority = LEVEL_PRIORITY[options.level];
    this.sink = options.sink ?? { write: (line) => process.stdout.write(line) };
    this.fields = Object.freeze({ ...options.fields });
    this.now = options.now ?? (() => new Date());
    this.service = options.service ?? 'ai-assistent';
  }

  child(fields: LogFields): Logger {
    return new JsonLineLogger({
      level: this.level,
      sink: this.sink,
      fields: { ...this.fields, ...fields },
      now: this.now,
      service: this.service,
    });
  }

  debug(message: string, fields?: LogFields): void {
    this.write('debug', message, fields);
  }

  info(message: string, fields?: LogFields): void {
    this.write('info', message, fields);
  }

  warn(message: string, fields?: LogFields): void {
    this.write('warn', message, fields);
  }

  error(message: string, fields?: LogFields): void {
    this.write('error', message, fields);
  }

  private write(level: LogLevel, message: string, fields?: LogFields): void {
    if (LEVEL_PRIORITY[level] < this.minPriority) {
      return;
    }
    const head = {
      time: this.now().toISOString(),
      level,
      service: this.service,
      msg: message,
    };
    try {
      const line = { ...head, ...serializeFields(this.fields), ...serializeFields(fields ?? {}) };
      this.sink.write(`${JSON.stringify(line)}\n`);
    } catch {
      // A field that cannot be read must never break the caller; the message still goes out.
      this.sink.write(`${JSON.stringify({ ...head, fields: null })}\n`);
    }
  }
}

/** Creates the root logger; `level` comes from the validated service configuration. */
export function createLogger(options: LoggerOptions): Logger {
  return new JsonLineLogger(options);
}
