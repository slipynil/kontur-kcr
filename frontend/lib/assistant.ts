import { APICallError, DefaultChatTransport } from 'ai';
import { ApiError, api } from './api';

// Client tool of the chat stream that asks the user for missing data (MCP input required).
export const inputTool = 'request_input';
export const clientNotFound = 'Клиент не найден';
export const assistantUnavailable = 'Помощник временно недоступен, повторите позже';
const connectionLost = 'Не удалось связаться с сервером. Проверьте соединение и повторите попытку.';
const chatRejected = 'Помощник не смог обработать запрос. Переформулируйте вопрос.';
const chatInterrupted = 'Помощник не завершил ответ. Повторите вопрос позже.';

function statusText(status: number | undefined, detail: string): string {
  if (status === 404) return clientNotFound;
  if (status === 403) return 'Недостаточно прав для обращения к помощнику';
  if (status === 401) return 'Сессия истекла. Войдите снова.';
  // 502 and 503: the Gateway or the assistant service did not answer.
  if (status === undefined || status >= 500) return assistantUnavailable;
  return detail;
}

// The message of a failed fetch request in Chromium, Firefox, Safari and Node.js.
function connectionFailed(error: unknown) {
  return error instanceof TypeError && /fetch|network|load failed/i.test(error.message);
}

/** A readable reason for a failed summary or selection request; `fallback` replaces a rejected request without an explanation. */
export function requestFailure(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    // Without a server explanation `request` reports the status only.
    const detail = error.message === `Ошибка запроса (${error.status})` ? '' : error.message;
    return statusText(error.status, detail) || fallback;
  }
  if (connectionFailed(error)) return connectionLost;
  return error instanceof Error && error.message ? error.message : fallback;
}

/** A readable reason for a failed chat request; the text of a broken stream stays out of the interface. */
export function chatFailure(error: unknown): string {
  if (APICallError.isInstance(error)) return statusText(error.statusCode, chatRejected);
  if (connectionFailed(error)) return connectionLost;
  return chatInterrupted;
}

/** Transport of the chat contract: POST /api/v1/assistant/chat/{kcr_code} with the dialogue only. */
export function createChatTransport(code: string) {
  return new DefaultChatTransport({
    api: `/assistant/chat/${encodeURIComponent(code)}`,
    // The contract carries only `messages`; the service fields of the SDK (id, trigger, messageId) are dropped.
    prepareSendMessagesRequest: ({ messages }) => ({ body: { messages } }),
    // The same API base, session token and 401 handling as every other request of the application.
    fetch: (input, init) => api(String(input), init),
  });
}
