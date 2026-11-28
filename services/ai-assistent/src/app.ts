import type { AddressInfo } from 'node:net';
import type { Config } from './config.js';
import { createLogger, type Logger } from './log.js';
import { HTTP_HOST, closeServer, createHttpServer, listen } from './http/server.js';
import { healthRoute } from './http/routes.js';
import { createMcpServer, MCP_HOST } from './mcp.js';

/** Upper bound of a graceful shutdown; connections still open are dropped afterwards. */
export const SHUTDOWN_TIMEOUT_MS = 10_000;

export interface App {
  readonly config: Config;
  readonly logger: Logger;
  /** Binds the ports. Call once; a `stop()` that arrives first is honoured when the bind finishes. */
  start(): Promise<void>;
  /** Stops accepting requests, lets in-flight ones finish and releases the ports. */
  stop(): Promise<void>;
  /** Address the public HTTP server is bound to, or `null` while it is not listening. */
  httpAddress(): AddressInfo | null;
}

export interface AppDependencies {
  logger?: Logger;
}

/** Wires the process together: configuration in, one start/stop handle out. */
export function createApp(config: Config, deps: AppDependencies = {}): App {
  const logger = deps.logger ?? createLogger({ level: config.log.level });
  const httpServer = createHttpServer({ logger, routes: [healthRoute()] });
  let mcp: Awaited<ReturnType<typeof createMcpServer>> | undefined;
  let stopping = false;

  return {
    config,
    logger,
    async start() {
      mcp = await createMcpServer(config);
      if (stopping) { await mcp.close(); return; }
      await listen(mcp.server, MCP_HOST, config.mcpPort);
      if (stopping) { await closeServer(mcp.server, SHUTDOWN_TIMEOUT_MS); await mcp.close(); return; }
      try {
      await listen(httpServer, HTTP_HOST, config.port);
      } catch (error) {
        await closeServer(mcp.server, SHUTDOWN_TIMEOUT_MS);
        await mcp.close();
        throw error;
      }
      if (!httpServer.listening) {
        if (stopping) {
          // `stop()` closed the socket before it could bind.
          return;
        }
        throw new Error('HTTP server closed before it started listening');
      }
      if (stopping) {
        // `stop()` ran before the port was bound; release what the late bind took.
        await closeServer(httpServer, SHUTDOWN_TIMEOUT_MS);
        return;
      }
      const address = httpServer.address();
      logger.info('http server listening', {
        host: HTTP_HOST,
        port: address !== null && typeof address !== 'string' ? address.port : config.port,
      });
    },
    async stop() {
      stopping = true;
      await closeServer(httpServer, SHUTDOWN_TIMEOUT_MS);
      if (mcp) { await closeServer(mcp.server, SHUTDOWN_TIMEOUT_MS); await mcp.close(); }
      logger.info('http server stopped');
    },
    httpAddress() {
      return httpServer.listening ? (httpServer.address() as AddressInfo) : null;
    },
  };
}
