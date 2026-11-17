import { web } from '@e2e-dev/web';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';
import { accounts, appUrl } from './tests/kontur-fixtures.ts';

const { MODEL_API_KEY, OPEN_BASE_URL, CHAT_MODEL, MODEL_NAME } = process.env;

/** @satisfies {import('e2e').E2EConfig} */
export default {
  agents: OPEN_BASE_URL && CHAT_MODEL ? {
    default: {
      model: createOpenAICompatible({
        name: MODEL_NAME ?? 'OpenAI comparable',
        baseURL: OPEN_BASE_URL,
        apiKey: MODEL_API_KEY,
      }).chatModel(CHAT_MODEL),
      system: 'You are a thorough QA agent. Verify every outcome.',
    },
  } : undefined,
  credentials: Object.fromEntries(Object.entries(accounts).map(([name, account]) => [
    name, { username: account.login, password: account.password },
  ])),
  targets: [{ engine: web(), app: { url: appUrl } }],
};
