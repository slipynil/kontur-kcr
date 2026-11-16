import type { E2EConfig } from 'e2e';
import { web } from '@e2e-dev/web';
import { createOpenAICompatible } from '@ai-sdk/openai-compatible';

// e2e loads no .env itself: pull the model key (ZAI_API_KEY) from it when present.
// In CI the file is absent and the variables come from the environment.
try { process.loadEnvFile(); } catch { /* no .env; env comes from the shell or CI */ }

export default {
  // The endpoint serves the model id over the OpenAI chat API; pass apiKey when it needs one.
  agents: {
    default: {
      model: createOpenAICompatible({
        name: process.env.MODEL_NAME ?? 'OpenAI comparable',
        baseURL: process.env.OPEN_BASE_URL,
        apiKey: process.env.ZAI_API_KEY,
      }).chatModel(process.env.CHAT_MODEL),
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
