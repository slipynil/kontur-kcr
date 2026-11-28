import { createApp } from './app.js';
import { ConfigError, loadConfig } from './config.js';
import { runUntilShutdown } from './lifecycle.js';
import { createLogger } from './log.js';

async function main(): Promise<number> {
  let config;
  try {
    config = loadConfig();
  } catch (error) {
    if (error instanceof ConfigError) {
      process.stderr.write(`${error.message}\n`);
      return 1;
    }
    throw error;
  }
  const logger = createLogger({ level: config.log.level });
  return runUntilShutdown(createApp(config, { logger }), { logger });
}

void main().then(
  (code) => process.exit(code),
  (error: unknown) => {
    const detail = error instanceof Error ? (error.stack ?? error.message) : String(error);
    process.stderr.write(`ai-assistent failed: ${detail}\n`);
    process.exit(1);
  },
);
