import { describe, expect, it } from '@jest/globals';
import {
  CONFIG_VARIABLES,
  ConfigError,
  DEFAULT_MCP_PORT,
  DEFAULT_PORT,
  parseConfig,
} from '../src/config.js';

function issuesOf(env: Record<string, string>): ConfigError['issues'] {
  try {
    parseConfig(env);
  } catch (error) {
    if (error instanceof ConfigError) {
      return error.issues;
    }
    throw error;
  }
  throw new Error('parseConfig was expected to fail');
}

describe('parseConfig', () => {
  it('applies the documented defaults when nothing is set', () => {
    expect(parseConfig({})).toEqual({
      port: DEFAULT_PORT,
      mcpPort: DEFAULT_MCP_PORT,
      log: { level: 'info' },
    });
    expect(DEFAULT_PORT).toBe(8000);
    expect(DEFAULT_MCP_PORT).toBe(8001);
  });

  it('reads explicit values', () => {
    expect(
      parseConfig({ ASSISTANT_PORT: ' 9000 ', ASSISTANT_MCP_PORT: '9001', AI_ASSISTENT_LOG_LEVEL: 'WARN' }),
    ).toEqual({ port: 9000, mcpPort: 9001, log: { level: 'warn' } });
  });

  it('treats blank values as unset', () => {
    expect(parseConfig({ ASSISTANT_PORT: '   ', AI_ASSISTENT_LOG_LEVEL: '' })).toEqual({
      port: DEFAULT_PORT,
      mcpPort: DEFAULT_MCP_PORT,
      log: { level: 'info' },
    });
  });

  it('ignores variables it does not own', () => {
    expect(parseConfig({ PATH: '/usr/bin', ASSISTANT_MCP_PORT: '8100' }).mcpPort).toBe(8100);
  });

  it('rejects a port outside the TCP range', () => {
    expect(issuesOf({ ASSISTANT_MCP_PORT: '70000' })).toEqual([
      { variable: 'ASSISTANT_MCP_PORT', message: expect.any(String) },
    ]);
    expect(issuesOf({ ASSISTANT_PORT: '0' })).toHaveLength(1);
  });

  it('rejects a value that is not an integer', () => {
    expect(issuesOf({ ASSISTANT_PORT: '8000.5' })).toHaveLength(1);
    expect(issuesOf({ ASSISTANT_PORT: 'http://8000' })).toHaveLength(1);
  });

  it('rejects an unknown log level', () => {
    expect(issuesOf({ AI_ASSISTENT_LOG_LEVEL: 'verbose' })).toEqual([
      { variable: 'AI_ASSISTENT_LOG_LEVEL', message: expect.any(String) },
    ]);
  });

  it('reports every problem at once, naming each variable', () => {
    const error = (() => {
      try {
        parseConfig({ ASSISTANT_PORT: '-1', AI_ASSISTENT_LOG_LEVEL: 'loud' });
      } catch (caught) {
        return caught;
      }
      throw new Error('parseConfig was expected to fail');
    })();
    expect(error).toBeInstanceOf(ConfigError);
    const configError = error as ConfigError;
    expect(configError.name).toBe('ConfigError');
    expect(configError.issues.map((issue) => issue.variable)).toEqual([
      'ASSISTANT_PORT',
      'AI_ASSISTENT_LOG_LEVEL',
    ]);
    expect(configError.message).toBe(
      [
        'Invalid ai-assistent configuration:',
        `  - ASSISTANT_PORT: ${configError.issues[0]?.message}`,
        `  - AI_ASSISTENT_LOG_LEVEL: ${configError.issues[1]?.message}`,
      ].join('\n'),
    );
  });

  it('returns a frozen configuration', () => {
    const config = parseConfig({});
    expect(Object.isFrozen(config)).toBe(true);
    expect(Object.isFrozen(config.log)).toBe(true);
  });

  it('lists its variables under the names the README documents', () => {
    expect(CONFIG_VARIABLES).toEqual(['ASSISTANT_PORT', 'ASSISTANT_MCP_PORT', 'AI_ASSISTENT_LOG_LEVEL']);
  });
});
