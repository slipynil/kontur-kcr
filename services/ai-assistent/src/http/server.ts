import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import type { Logger, LogLevel } from '../log.js';

/** Body shape of the `Error` schema from docs/api/internal/assistant-service.yaml. */
export interface ErrorBody {
  readonly code: string;
  readonly message: string;
}

export interface RouteContext {
  logger: Logger;
}

export interface Route {
  method: string;
  /** Exact request path, without a query string. */
  path: string;
  /** Level of the access line; health checks log at `debug` to stay quiet. */
  accessLog?: LogLevel;
  handle(req: IncomingMessage, res: ServerResponse, context: RouteContext): Promise<void> | void;
}

export interface HttpServerOptions {
  logger: Logger;
  routes: readonly Route[];
}

export const HTTP_HOST = '0.0.0.0';

export const NOT_FOUND: ErrorBody = { code: 'NOT_FOUND', message: 'Ресурс не найден' };
export const METHOD_NOT_ALLOWED: ErrorBody = { code: 'METHOD_NOT_ALLOWED', message: 'Метод не поддерживается' };
export const INTERNAL_ERROR: ErrorBody = { code: 'INTERNAL_ERROR', message: 'Внутренняя ошибка сервера' };

/** Writes a JSON body with headers that keep clients and proxies from caching it. */
export function sendJson(res: ServerResponse, status: number, body: unknown): void {
  if (res.headersSent) {
    res.end();
    return;
  }
  const payload = Buffer.from(JSON.stringify(body));
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': String(payload.length),
    'cache-control': 'no-store',
  });
  res.end(payload);
}

function pathnameOf(target: string | undefined): string {
  const raw = target ?? '/';
  const queryIndex = raw.indexOf('?');
  return queryIndex === -1 ? raw : raw.slice(0, queryIndex);
}

/**
 * Minimal HTTP server on Node built-ins: exact-path routes, an access line per
 * request (method, path, status and duration only — never headers or bodies)
 * and a JSON error for everything else.
 */
export function createHttpServer(options: HttpServerOptions): Server {
  const { logger, routes } = options;
  const routesByPath = new Map<string, Route[]>();
  for (const route of routes) {
    const group = routesByPath.get(route.path) ?? [];
    group.push(route);
    routesByPath.set(route.path, group);
  }

  const server = createServer((req, res) => {
    void handleRequest(req, res);
  });

  async function handleRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const startedAt = performance.now();
    const path = pathnameOf(req.url);
    let accessLog: LogLevel = 'info';
    res.once('finish', () => {
      logger[accessLog]('http request', {
        method: req.method,
        path,
        status: res.statusCode,
        durationMs: Math.round(performance.now() - startedAt),
      });
    });

    try {
      const candidates = routesByPath.get(path);
      if (!candidates) {
        sendJson(res, 404, NOT_FOUND);
        return;
      }
      const route = candidates.find((candidate) => candidate.method === req.method);
      if (!route) {
        res.setHeader('Allow', candidates.map((candidate) => candidate.method).join(', '));
        sendJson(res, 405, METHOD_NOT_ALLOWED);
        return;
      }
      accessLog = route.accessLog ?? 'info';
      await route.handle(req, res, { logger });
    } catch (error) {
      logger.error('http request failed', { method: req.method, path, error });
      if (res.headersSent) {
        res.destroy();
      } else {
        sendJson(res, 500, INTERNAL_ERROR);
      }
    }
  }

  return server;
}

/** Resolves once the port is bound — or at once if the server is closed before it binds; rejects on listen errors. */
export function listen(server: Server, host: string, port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const finish = (outcome: () => void): void => {
      server.off('error', onError);
      server.off('listening', onListening);
      server.off('close', onClose);
      outcome();
    };
    const onError = (error: Error): void => finish(() => reject(error));
    const onListening = (): void => finish(() => resolve());
    const onClose = (): void => finish(() => resolve());
    server.once('error', onError);
    server.once('listening', onListening);
    server.once('close', onClose);
    server.listen(port, host);
  });
}

/**
 * Stops accepting connections and lets in-flight requests finish. Connections
 * still open after `timeoutMs` are dropped so shutdown always completes.
 */
export function closeServer(server: Server, timeoutMs: number): Promise<void> {
  if (!server.listening) {
    // Never started or already closed: `Server.close` would wait for a `close` event that never comes.
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    const forceTimer = setTimeout(() => server.closeAllConnections(), timeoutMs);
    server.close(() => {
      clearTimeout(forceTimer);
      resolve();
    });
    server.closeIdleConnections();
  });
}
