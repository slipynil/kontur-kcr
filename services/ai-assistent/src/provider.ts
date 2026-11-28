import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import type { Config } from './config.js';
import { ReadError } from './upstreams.js';

/** No implicit provider or model: the project owner configures the agreed endpoint. */
export function createModel(config: Config['provider']) {
  if (!config.baseURL || !config.apiKey || !config.model) throw new ReadError('ASSISTANT_UNAVAILABLE');
  return createOpenAICompatible({ name: 'configured-provider', baseURL: config.baseURL, apiKey: config.apiKey })(config.model);
}
