import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';

export default {
  // The endpoint serves the model id over the OpenAI chat API; pass apiKey when it needs one.
  agents: {
    default: {
      model: createOpenAICompatible({
        name: 'openai-compatible',
        baseURL: 'https://api.z.ai/api/coding/paas/v4',
        // apiKey: process.env.LLM_API_KEY,
      }).chatModel('gpt-6-luna'),
      system: 'You are a thorough QA agent. Verify every outcome.',
    },
  },
  // Local dev admin account; E2E_USER_ADMIN_USERNAME / E2E_USER_ADMIN_PASSWORD override it.
  credentials: {
    admin: { username: 'admin', password: process.env.E2E_ADMIN_PASSWORD ?? 'password123' },
  },
  targets: [{
    engine: web(),
    app: {
      // The stack is already up (frontend :80, gateway :8888); the runner only attaches.
      url: process.env.APP_URL ?? 'http://localhost:80',
    },
  }],
} satisfies E2EConfig;
