import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';
import { McpServer, createMcpHandler, inputRequired, acceptedContent, inputResponse,
  type CallToolResult, type InputRequiredResult } from '@modelcontextprotocol/server';
import { loadSkills } from '@modelcontextprotocol/server/ext/skills/node';
import { toNodeHandler, localhostHostValidation, localhostOriginValidation } from '@modelcontextprotocol/node';
import * as z from 'zod/v4';
import type { Config } from './config.js';
import { ReadApi, ReadError, identitySchema } from './upstreams.js';
import { ReadTools, criteriaSchema, pageInput, quarter } from './tools.js';

export const MCP_HOST = '127.0.0.1';
const annotations = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: false };
const result = (value: object): CallToolResult => ({ content: [{ type: 'text', text: JSON.stringify(value) }], structuredContent: value });
// Elicitation's restricted wire schema cannot carry a regex. Validate the answer separately.
const periodSchema = z.object({ quarter: z.string().meta({ title: 'Исходный квартал документов (YYYY_QN)' }) });

export async function createMcpServer(config: Config) {
  const skills = await loadSkills(fileURLToPath(new URL('../skills', import.meta.url)), { strict: true });
  const handler = createMcpHandler(({ requestInfo }) => {
    const server = new McpServer({ name: 'ai-assistent', version: '0.1.0' });
    skills.attach(server);
    const tools = () => {
      const identity = identitySchema.safeParse({ userId: requestInfo?.headers.get('X-User-Id'), role: requestInfo?.headers.get('X-User-Role') });
      if (!identity.success) throw new ReadError('FORBIDDEN');
      // The host forwards the route's scope in a header, outside model-controlled arguments.
      const code = requestInfo?.headers.get('X-Assistant-Kcr-Code') ?? undefined;
      return new ReadTools(new ReadApi(config.upstreams, identity.data, requestInfo?.signal), code);
    };
    const safe = async (call: () => Promise<object>): Promise<CallToolResult> => {
      try { return result(await call()); }
      catch (error) {
        const code = error instanceof ReadError ? error.code : 'ASSISTANT_UNAVAILABLE';
        return { isError: true, content: [{ type: 'text', text: code }] };
      }
    };
    server.registerTool('get_client_card', { description: 'Актуальные документы и рабочее состояние клиента из маршрута.', inputSchema: z.object({}).strict(), annotations },
      () => safe(() => tools().getClientCard()));
    server.registerTool('get_call_context', { description: 'Факты для контекста перед звонком; неизвестные значения остаются null.', inputSchema: z.object({}).strict(), annotations },
      () => safe(() => tools().getCallContext()));
    server.registerTool('get_history_events', { description: 'Страница хроники клиента; quarter — квартал цикла работы.', inputSchema: pageInput, annotations },
      input => safe(() => tools().getHistoryEvents(input)));
    server.registerTool('get_snapshot_history', { description: 'Журнал наблюдений исходного квартала. Если квартал не задан, запросить его у пользователя.', inputSchema: pageInput, annotations },
      async (input, ctx): Promise<CallToolResult | InputRequiredResult> => {
        const response = inputResponse(ctx.mcpReq.inputResponses, 'period');
        if (!input.quarter && response.kind === 'elicit' && response.action !== 'accept') return result({ items: [], unavailable: 'Период не указан' });
        const answer = acceptedContent(ctx.mcpReq.inputResponses, 'period', periodSchema);
        if (!input.quarter && !answer) return inputRequired({ inputRequests: { period: inputRequired.elicit({
          message: 'За какой исходный квартал показать динамику?', requestedSchema: periodSchema,
        }) } });
        return safe(() => tools().getSnapshotHistory({ ...input, quarter: quarter.parse(input.quarter ?? answer?.quarter) }));
      });
    server.registerTool('query_clients', { description: 'Отбор ADMIN по фильтрам реестра. Ничего не публикует. Возвращает ограниченную выборку и полный total.',
      inputSchema: z.object({ criteria: criteriaSchema, limit: z.number().int().min(1).max(1000).default(200) }).strict(), annotations },
      input => safe(() => tools().queryClients(input.criteria, input.limit)));
    return server;
  }, { legacy: 'reject' });
  const nodeHandler = toNodeHandler(handler);
  const validHost = localhostHostValidation();
  const validOrigin = localhostOriginValidation();
  const server = createServer((req, res) => {
    if (!validHost(req, res) || !validOrigin(req, res)) return;
    if (req.url !== '/mcp') { res.writeHead(404).end(); return; }
    void nodeHandler(req, res);
  });
  return { server, close: () => handler.close() };
}
