import { Chat } from '@ai-sdk/react';
import { ApiError, saveSession } from '../lib/api';
import { assistantUnavailable, chatFailure, clientNotFound, createChatTransport, requestFailure } from '../lib/assistant';
import type { User } from '../lib/contracts';
import { answerChunks, sseResponse } from '../test-support/sse';

const user: User = { id: 'operator', login: 'operator', role: 'OPERATOR', is_active: true, created_at: '' };
const signIn = () => saveSession({ token: 'jwt', token_type: 'Bearer', expires_in: 3600, user });

beforeEach(() => { localStorage.clear(); global.fetch = jest.fn(); });

it('sends only the dialogue of the contract with the session token and reads the streamed answer', async () => {
  signIn();
  (fetch as jest.Mock).mockResolvedValue(sseResponse(...answerChunks));
  const chat = new Chat({ transport: createChatTransport('A/B') });

  await chat.sendMessage({ text: 'Как динамика?' });

  const [url, init] = (fetch as jest.Mock).mock.calls[0];
  expect(url).toBe('/api/v1/assistant/chat/A%2FB');
  expect(JSON.parse(init.body)).toEqual({ messages: [{ id: expect.any(String), role: 'user', parts: [{ type: 'text', text: 'Как динамика?' }] }] });
  expect(init.headers.get('authorization')).toBe('Bearer jwt');
  expect(init.headers.get('content-type')).toBe('application/json');
  expect(init.cache).toBe('no-store');
  expect(chat.status).toBe('ready');
  expect(chat.messages.at(-1)).toEqual({
    id: 'answer-1', role: 'assistant', metadata: undefined,
    parts: [{ type: 'text', text: 'Непереданных документов: 12 из 40.', state: 'done', providerMetadata: undefined }],
  });
});

it('clears the session when the chat request is rejected as unauthorized', async () => {
  signIn();
  (fetch as jest.Mock).mockResolvedValue(new Response(JSON.stringify({ code: 'UNAUTHORIZED', message: 'Войдите снова' }), { status: 401 }));
  const chat = new Chat({ transport: createChatTransport('A') });

  await chat.sendMessage({ text: 'Вопрос' });

  expect(localStorage.getItem('kontur-api-token')).toBeNull();
  expect(chatFailure(chat.error)).toBe('Сессия истекла. Войдите снова.');
});

it.each([[404, clientNotFound], [502, assistantUnavailable], [503, assistantUnavailable]])(
  'explains the chat failure of HTTP %i', async (status, text) => {
    (fetch as jest.Mock).mockResolvedValue(new Response('<html>unavailable</html>', { status }));
    const chat = new Chat({ transport: createChatTransport('A') });
    await chat.sendMessage({ text: 'Вопрос' });
    expect(chat.status).toBe('error');
    expect(chatFailure(chat.error)).toBe(text);
    // The failed turn stays in the dialogue to repeat it.
    expect(chat.messages).toHaveLength(1);
    expect((fetch as jest.Mock).mock.calls[0][1].headers.has('authorization')).toBe(false);
  },
);

it('does not show the text of a broken stream or of a lost connection', async () => {
  (fetch as jest.Mock).mockResolvedValueOnce(sseResponse({ type: 'error', errorText: 'upstream token rejected' }));
  const chat = new Chat({ transport: createChatTransport('A') });
  await chat.sendMessage({ text: 'Вопрос' });
  expect(chat.status).toBe('error');
  expect(chatFailure(chat.error)).toBe('Помощник не завершил ответ. Повторите вопрос позже.');

  (fetch as jest.Mock).mockRejectedValueOnce(new TypeError('Failed to fetch'));
  await chat.sendMessage({ text: 'Ещё вопрос' });
  expect(chatFailure(chat.error)).toBe('Не удалось связаться с сервером. Проверьте соединение и повторите попытку.');
});

describe('requestFailure', () => {
  it.each([
    [new ApiError(404, 'NOT_FOUND', 'Объект не найден'), clientNotFound],
    [new ApiError(503, 'ASSISTANT_UNAVAILABLE', 'Сервис временно недоступен, повторите позже'), assistantUnavailable],
    [new ApiError(502, 'HTTP_ERROR', 'Ошибка запроса (502)'), assistantUnavailable],
    [new ApiError(400, 'INVALID_QUERY', 'Не удалось разобрать текст запроса'), 'Не удалось разобрать текст запроса'],
    [new ApiError(400, 'VALIDATION_ERROR', 'Ошибка запроса (400)'), 'Не удалось разобрать текст запроса'],
    [new ApiError(403, 'FORBIDDEN', 'Недостаточно прав'), 'Недостаточно прав для обращения к помощнику'],
    [new TypeError('NetworkError when attempting to fetch resource.'), 'Не удалось связаться с сервером. Проверьте соединение и повторите попытку.'],
    [new Error('Сервер вернул некорректный ответ'), 'Сервер вернул некорректный ответ'],
  ])('explains %p', (error, text) => {
    expect(requestFailure(error, 'Не удалось разобрать текст запроса')).toBe(text);
  });
});
