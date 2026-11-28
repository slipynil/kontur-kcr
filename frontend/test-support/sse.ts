// A chat answer as the Gateway sends it (docs/api/public-api.yaml, assistantChat):
// Server-Sent Events with one `data:` line per part and the closing `[DONE]` marker.
export function sseStream(...chunks: unknown[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(`data: ${typeof chunk === 'string' ? chunk : JSON.stringify(chunk)}\n\n`));
      }
      controller.enqueue(encoder.encode('data: [DONE]\n\n'));
      controller.close();
    },
  });
}

export function sseResponse(...chunks: unknown[]): Response {
  return new Response(sseStream(...chunks), {
    headers: {
      'content-type': 'text/event-stream', 'cache-control': 'no-cache', 'x-accel-buffering': 'no', 'x-vercel-ai-ui-message-stream': 'v1',
    },
  });
}

// A complete answer: the text is sent in two parts.
export const answerChunks = [
  { type: 'start', messageId: 'answer-1' },
  { type: 'text-start', id: 'text-1' },
  { type: 'text-delta', id: 'text-1', delta: 'Непереданных документов: ' },
  { type: 'text-delta', id: 'text-1', delta: '12 из 40.' },
  { type: 'text-end', id: 'text-1' },
  { type: 'finish' },
];

export const inputRequest = {
  key: 'quarter',
  message: 'За какой квартал показать динамику?',
  requested_schema: {
    type: 'object',
    properties: { quarter: { type: 'string', title: 'Квартал', enum: ['2026_Q3', '2026_Q4'] } },
    required: ['quarter'],
  },
};

// The assistant reads the client, asks for the missing data and ends the turn.
export const inputChunks = [
  { type: 'start', messageId: 'answer-1' },
  { type: 'start-step' },
  { type: 'tool-input-start', toolCallId: 'call-1', toolName: 'get_client_card' },
  { type: 'tool-input-available', toolCallId: 'call-1', toolName: 'get_client_card', input: { kcr_code: 'A' } },
  { type: 'tool-output-available', toolCallId: 'call-1', output: { unsubmitted_documents: 12 } },
  { type: 'start-step' },
  { type: 'tool-input-start', toolCallId: 'call-2', toolName: 'request_input' },
  { type: 'tool-input-available', toolCallId: 'call-2', toolName: 'request_input', input: inputRequest },
  { type: 'finish-step' },
  { type: 'finish' },
];

// The turn continues with the same message id after the answer to `request_input`.
export const continuedChunks = [
  { type: 'start', messageId: 'answer-1' },
  { type: 'data-event-references', data: [
    { event_id: 'e1', event_date: '2026-10-28T09:30:00Z', quarter: '2026_Q4' },
    { event_id: 'e2', event_date: '2026-11-10T14:00:00Z' },
  ] },
  ...answerChunks.slice(1),
];
