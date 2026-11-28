import * as z from 'zod/v4';

export const identitySchema = z.object({ userId: z.uuid(), role: z.enum(['ADMIN', 'OPERATOR']) });
export type Identity = z.infer<typeof identitySchema>;
export interface UpstreamConfig {
  clients: string;
  operatorWork: string;
  clientHistory: string;
  timeoutMs: number;
}

export class ReadError extends Error {
  readonly code: 'NOT_FOUND' | 'ASSISTANT_UNAVAILABLE' | 'FORBIDDEN';
  constructor(code: ReadError['code']) {
    super(code);
    this.code = code;
  }
}

/** An allowlist of read operations: the only POST is the existing batch read. */
export class ReadApi {
  private readonly config: UpstreamConfig;
  readonly identity: Identity;
  private readonly signal?: AbortSignal;
  constructor(
    config: UpstreamConfig, identity: Identity, signal?: AbortSignal,
  ) { this.config = config; this.identity = identity; this.signal = signal; }

  async get<T>(service: keyof Omit<UpstreamConfig, 'timeoutMs'>, path: string, schema: z.ZodType<T>): Promise<T> {
    return this.read(service, path, schema);
  }

  async assignees(codes: string[]) {
    return this.read('operatorWork', '/clients/current-assignees', z.object({
      items: z.array(z.object({ kcr_code: z.string(), assigned_operator_id: z.uuid() })),
    }), { codes });
  }

  private async read<T>(service: keyof Omit<UpstreamConfig, 'timeoutMs'>, path: string, schema: z.ZodType<T>, body?: { codes: string[] }): Promise<T> {
    try {
      const timeout = AbortSignal.timeout(this.config.timeoutMs);
      const response = await fetch(`${this.config[service]}/internal/v1${path}`, {
        method: body ? 'POST' : 'GET',
        headers: { 'X-User-Id': this.identity.userId, 'X-User-Role': this.identity.role,
          ...(body ? { 'Content-Type': 'application/json' } : {}) },
        ...(body ? { body: JSON.stringify(body) } : {}),
        signal: this.signal ? AbortSignal.any([this.signal, timeout]) : timeout,
        redirect: 'error',
      });
      if (response.status === 404) throw new ReadError('NOT_FOUND');
      if (!response.ok) throw new ReadError('ASSISTANT_UNAVAILABLE');
      return schema.parse(await response.json());
    } catch (error) {
      if (error instanceof ReadError) throw error;
      // Never return upstream bodies, URLs or credentials through the tool error.
      throw new ReadError('ASSISTANT_UNAVAILABLE');
    }
  }
}
