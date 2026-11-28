import type { App } from './app.js';
import type { Logger } from './log.js';

export const SHUTDOWN_SIGNALS: readonly NodeJS.Signals[] = ['SIGTERM', 'SIGINT'];

/** Where the shutdown signals come from; the process by default. */
export interface SignalSource {
  on(event: NodeJS.Signals, listener: (signal: NodeJS.Signals) => void): unknown;
  off(event: NodeJS.Signals, listener: (signal: NodeJS.Signals) => void): unknown;
}

export interface RunOptions {
  logger: Logger;
  signals?: SignalSource;
}

/**
 * Starts the app, waits for SIGTERM or SIGINT, stops the app and resolves with
 * the process exit code. A repeated signal is left to the process default and
 * terminates at once.
 */
export function runUntilShutdown(app: App, options: RunOptions): Promise<number> {
  const { logger } = options;
  const signals = options.signals ?? process;

  return new Promise((resolve) => {
    const removeListeners = (): void => {
      for (const signal of SHUTDOWN_SIGNALS) {
        signals.off(signal, onSignal);
      }
    };

    const shutdown = async (): Promise<void> => {
      try {
        await app.stop();
        logger.info('service stopped');
        removeListeners();
        resolve(0);
      } catch (error) {
        logger.error('service shutdown failed', { error });
        removeListeners();
        resolve(1);
      }
    };

    const onSignal = (signal: NodeJS.Signals): void => {
      logger.info('received signal, shutting down', { signal });
      removeListeners();
      void shutdown();
    };

    for (const signal of SHUTDOWN_SIGNALS) {
      signals.on(signal, onSignal);
    }

    void app.start().then(
      () => {
        logger.info('service started', { port: app.httpAddress()?.port });
      },
      (error: unknown) => {
        logger.error('service failed to start', { error });
        removeListeners();
        resolve(1);
      },
    );
  });
}
