import * as z from 'zod/v4';
import { LOG_LEVELS, type LogLevel } from './log.js';
import type { UpstreamConfig } from './upstreams.js';

export const SERVICE_NAME = 'ai-assistent';

/** The service listens on every interface: the Gateway reaches it over the container network. */
export const DEFAULT_PORT = 8000;
/** The embedded MCP server listens on loopback only (ADR-0022). */
export const DEFAULT_MCP_PORT = 8001;

export interface Config {
  readonly upstreams: UpstreamConfig;
  readonly provider: { readonly baseURL?: string; readonly apiKey?: string; readonly model?: string };
  /** Port of the public HTTP server (`0.0.0.0`). */
  readonly port: number;
  /** Port of the embedded MCP server on `127.0.0.1`. */
  readonly mcpPort: number;
  readonly log: {
    readonly level: LogLevel;
  };
}

export interface ConfigIssue {
  readonly variable: string;
  readonly message: string;
}

/** Thrown at startup when an environment variable is missing or malformed. */
export class ConfigError extends Error {
  readonly issues: readonly ConfigIssue[];

  constructor(issues: readonly ConfigIssue[]) {
    super(formatConfigError(issues));
    this.name = 'ConfigError';
    this.issues = issues;
  }
}

function formatConfigError(issues: readonly ConfigIssue[]): string {
  const lines = issues.map((issue) => `  - ${issue.variable}: ${issue.message}`);
  return `Invalid ${SERVICE_NAME} configuration:\n${lines.join('\n')}`;
}

const portSchema = z.coerce.number().int().min(1).max(65535);

const logLevelSchema = z.preprocess(
  (value) => (typeof value === 'string' ? value.trim().toLowerCase() : value),
  z.enum(LOG_LEVELS),
);

/** Environment variables of the service; object keys are the variable names. */
const schema = z.object({
  CLIENTS_ENDPOINT: z.url().default('http://clients:8000'),
  OPERATOR_WORK_ENDPOINT: z.url().default('http://operator-work:8000'),
  CLIENT_HISTORY_ENDPOINT: z.url().default('http://client-history:8000'),
  ASSISTANT_UPSTREAM_TIMEOUT_MS: z.coerce.number().int().min(1).max(300000).default(10000),
  ASSISTANT_PROVIDER_BASE_URL: z.url().optional(),
  ASSISTANT_PROVIDER_API_KEY: z.string().optional(),
  ASSISTANT_MODEL: z.string().optional(),
  ASSISTANT_PORT: portSchema.default(DEFAULT_PORT),
  ASSISTANT_MCP_PORT: portSchema.default(DEFAULT_MCP_PORT),
  AI_ASSISTENT_LOG_LEVEL: logLevelSchema.default('info'),
});

export const CONFIG_VARIABLES: readonly string[] = Object.keys(schema.shape);

/** Reads only the service variables; empty values count as unset. */
function pickEnv(env: Readonly<Record<string, string | undefined>>): Record<string, string> {
  const picked: Record<string, string> = {};
  for (const variable of CONFIG_VARIABLES) {
    const value = env[variable];
    if (value !== undefined && value.trim() !== '') {
      picked[variable] = value.trim();
    }
  }
  return picked;
}

/** Validates the given environment and returns the frozen configuration. */
export function parseConfig(env: Readonly<Record<string, string | undefined>>): Config {
  const result = schema.safeParse(pickEnv(env));
  if (!result.success) {
    throw new ConfigError(
      result.error.issues.map((issue) => ({
        variable: issue.path.join('.'),
        message: issue.message,
      })),
    );
  }
  const value = result.data;
  for (const variable of ['CLIENTS_ENDPOINT', 'OPERATOR_WORK_ENDPOINT', 'CLIENT_HISTORY_ENDPOINT', 'ASSISTANT_PROVIDER_BASE_URL'] as const) {
    const raw = value[variable];
    if (raw && (!['http:', 'https:'].includes(new URL(raw).protocol) || new URL(raw).username || new URL(raw).password || new URL(raw).search || new URL(raw).hash)) {
      throw new ConfigError([{ variable, message: 'Expected an HTTP(S) URL without credentials, query or fragment' }]);
    }
  }
  return Object.freeze({
    upstreams: Object.freeze({ clients: value.CLIENTS_ENDPOINT.replace(/\/$/, ''),
      operatorWork: value.OPERATOR_WORK_ENDPOINT.replace(/\/$/, ''),
      clientHistory: value.CLIENT_HISTORY_ENDPOINT.replace(/\/$/, ''), timeoutMs: value.ASSISTANT_UPSTREAM_TIMEOUT_MS }),
    provider: Object.freeze({ baseURL: value.ASSISTANT_PROVIDER_BASE_URL,
      apiKey: value.ASSISTANT_PROVIDER_API_KEY, model: value.ASSISTANT_MODEL }),
    port: value.ASSISTANT_PORT,
    mcpPort: value.ASSISTANT_MCP_PORT,
    log: Object.freeze({ level: value.AI_ASSISTENT_LOG_LEVEL }),
  });
}

/** Loads the configuration from the process environment; throws {@linkcode ConfigError}. */
export function loadConfig(env: Readonly<Record<string, string | undefined>> = process.env): Config {
  return parseConfig(env);
}
