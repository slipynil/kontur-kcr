import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';
import { createServer, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';
import { Client, StreamableHTTPClientTransport } from '@modelcontextprotocol/client';
import { SkillsClient } from '@modelcontextprotocol/client/ext/skills';
import { createMcpServer } from '../src/mcp.js';
import { listen, closeServer } from '../src/http/server.js';
import { testConfig } from './helpers.js';
import { createModel } from '../src/provider.js';
import { parseConfig } from '../src/config.js';

const userId = '11111111-1111-4111-8111-111111111111';
const id = '22222222-2222-4222-8222-222222222222';
const kcr = '6671-0042';
const row = (code = kcr, tags: string[] = []) => ({ kcr_code: code, tags, quarter_summary: null });
const observation = { id, kcr_code: kcr, quarter: '2026_Q4', unsubmitted_documents: 3,
  snapshot_date: '2026-11-28', created_at: '2026-11-28T12:00:00Z' };
const calls: { path: string; method?: string; user?: string; role?: string; body: string }[] = [];
let fault = '';
let upstream: Server;
let mcp: Awaited<ReturnType<typeof createMcpServer>>;
let base: string;
const clients: Client[] = [];

beforeEach(async () => {
  calls.length = 0; fault = '';
  upstream = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += String(chunk);
    calls.push({ path: req.url!, method: req.method, user: req.headers['x-user-id'] as string,
      role: req.headers['x-user-role'] as string, body });
    const url = new URL(req.url!, 'http://local');
    res.setHeader('content-type', 'application/json');
    if (fault === 'unavailable') { res.writeHead(500).end('private upstream error'); return; }
    if (fault === 'missing') { res.writeHead(404).end('{}'); return; }
    if (fault === 'malformed') { res.end('{broken'); return; }
    let value: unknown;
    if (url.pathname.endsWith('/work-state')) value = { kcr_code: kcr, work_quarter: '2026_Q4',
      assigned_operator_id: null, stage: 'POOL', awaiting_balance: false };
    else if (url.pathname.endsWith('/snapshots/history')) value = { items: [observation,
      { ...observation, id: userId, unsubmitted_documents: 8 }], total: 2, limit: 2, offset: 0 };
    else if (url.pathname.endsWith('/events')) value = { items: [{ id, kcr_code: kcr,
      event_type: 'CALL_RESULT', occurred_at: '2026-11-27T10:00:00Z', payload: { promised_date: '2026-12-01' } }],
      total: fault === 'incomplete' ? 101 : 1, page: 1, page_size: 100 };
    else if (url.pathname.endsWith('/current-assignees')) value = { items: [{ kcr_code: 'c101', assigned_operator_id: userId }] };
    else if (url.pathname.endsWith('/clients')) {
      const page = Number(url.searchParams.get('page'));
      value = { items: page === 1 ? Array.from({ length: 100 }, (_, i) => row(`c${i}`, ['VIP'])) : [row('c100'), row('c101')],
        total: 102, page, page_size: 100 };
    } else value = { ...row(fault === 'wrong-client' ? 'other' : kcr), contacts: { private: 'omit' },
      snapshots: [{ quarter: '2026_Q4', total_documents: 10, unsubmitted_documents: 3, snapshot_date: '2026-11-28' }] };
    res.end(JSON.stringify(value));
  });
  await listen(upstream, '127.0.0.1', 0);
  const endpoint = `http://127.0.0.1:${(upstream.address() as AddressInfo).port}`;
  mcp = await createMcpServer(testConfig({ upstreams: { clients: endpoint, operatorWork: endpoint, clientHistory: endpoint, timeoutMs: 1000 } }));
  await listen(mcp.server, '127.0.0.1', 0);
  base = `http://127.0.0.1:${(mcp.server.address() as AddressInfo).port}/mcp`;
});
afterEach(async () => {
  await Promise.all(clients.splice(0).map(client => client.close()));
  await closeServer(mcp.server, 1000); await mcp.close(); await closeServer(upstream, 1000);
});
async function connect(role = 'ADMIN', scope: string | null = kcr, identity = userId) {
  const client = new Client({ name: 'acceptance', version: '1' }, {
    versionNegotiation: { mode: { pin: '2026-07-28' } }, capabilities: { elicitation: { form: {} } },
  });
  clients.push(client);
  await client.connect(new StreamableHTTPClientTransport(new URL(base), { requestInit: { headers: {
    'X-User-Id': identity, 'X-User-Role': role, ...(scope ? { 'X-Assistant-Kcr-Code': scope } : {}),
  } } }));
  return client;
}

describe('embedded MCP over HTTP', () => {
  it('lists five read-only tools and verified domain skills over the protocol', async () => {
    const client = await connect();
    const { tools } = await client.listTools();
    expect(tools.map(tool => tool.name).sort()).toEqual(['get_call_context', 'get_client_card', 'get_history_events', 'get_snapshot_history', 'query_clients']);
    expect(tools.every(tool => tool.annotations?.readOnlyHint && !tool.annotations?.destructiveHint)).toBe(true);
    const skills = new SkillsClient(client);
    const listing = await skills.list();
    expect(listing.skills).toHaveLength(4);
    for (const skill of listing.skills) expect((await skills.read(skill)).verified).toBe(true);
    expect(calls).toHaveLength(0);
  });
  it('reads scoped facts with verified identity, strips contacts, and never writes', async () => {
    const client = await connect('OPERATOR');
    const value = await client.callTool({ name: 'get_call_context', arguments: {} });
    expect(value.isError).not.toBe(true);
    expect(value.structuredContent).toMatchObject({ next_call_number: 2, history_complete: true,
      periods: [{ change: -5 }], last_call: { id } });
    expect(JSON.stringify(value)).not.toContain('private');
    expect(calls.every(call => call.user === userId && call.role === 'OPERATOR' && call.method === 'GET')).toBe(true);
    expect(calls.find(call => call.path.includes('/snapshots/history'))?.path).toContain('quarter=2026_Q4');
  });
  it('does not claim a complete call count from a partial history page', async () => {
    fault = 'incomplete';
    const client = await connect();
    expect((await client.callTool({ name: 'get_call_context', arguments: {} })).structuredContent)
      .toMatchObject({ next_call_number: null, history_complete: false });
  });
  it('applies post-filters before limit and counts matches across registry pages', async () => {
    const client = await connect('ADMIN', null);
    const value = await client.callTool({ name: 'query_clients', arguments: {
      criteria: { exclude_tags: ['VIP'], assignment: 'FREE', dynamics: 'NO_CHANGE' }, limit: 1,
    } });
    expect(value.structuredContent).toMatchObject({ items: [{ kcr_code: 'c100' }], total: 1, not_applied: [] });
    expect(calls.filter(call => call.path.includes('current-assignees'))).toHaveLength(2);
    expect(calls.filter(call => call.path.includes('/clients?')).every(call => call.path.includes('dynamics=NO_CHANGE'))).toBe(true);
  });
  it('reports work_quarter as not applied instead of silently claiming it', async () => {
    const client = await connect('ADMIN', null);
    const value = await client.callTool({ name: 'query_clients', arguments: { criteria: { work_quarter: '2025_Q1' } } });
    expect(value.structuredContent).toMatchObject({ criteria: {}, not_applied: [expect.stringContaining('work_quarter')] });
  });
  it('retries the MCP period elicitation with the user answer on a fresh request', async () => {
    const client = await connect();
    client.setRequestHandler('elicitation/create', async () => ({ action: 'accept', content: { quarter: '2026_Q4' } }));
    const value = await client.callTool({ name: 'get_snapshot_history', arguments: {} });
    expect(value).toMatchObject({ structuredContent: { items: [{ id }, { id: userId }] } });
    expect(calls).toHaveLength(1);
  });
  it.each(['decline', 'cancel'] as const)('does not fetch a period when the user chooses %s', async action => {
    const client = await connect();
    client.setRequestHandler('elicitation/create', async () => ({ action }));
    expect((await client.callTool({ name: 'get_snapshot_history', arguments: {} })).structuredContent)
      .toMatchObject({ items: [], unavailable: expect.any(String) });
    expect(calls).toHaveLength(0);
  });
  it.each(['OPERATOR', 'ADMIN'])('denies global selection for a client-scoped %s request', async role => {
    const client = await connect(role);
    expect((await client.callTool({ name: 'query_clients', arguments: { criteria: {} } })).isError).toBe(true);
    expect(calls).toHaveLength(0);
  });
  it('denies global operator selection and missing identity', async () => {
    const operator = await connect('OPERATOR', null);
    expect((await operator.callTool({ name: 'query_clients', arguments: { criteria: {} } })).isError).toBe(true);
    const anonymous = await connect('ADMIN', kcr, 'invalid');
    expect((await anonymous.callTool({ name: 'get_client_card', arguments: {} })).isError).toBe(true);
    expect(calls).toHaveLength(0);
  });
  it.each(['unavailable', 'malformed', 'wrong-client', 'missing'])('returns a safe tool error for %s', async failure => {
    fault = failure;
    const client = await connect();
    const value = await client.callTool({ name: 'get_client_card', arguments: {} });
    expect(value.isError).toBe(true);
    expect(value.content).toEqual([{ type: 'text', text: failure === 'missing' ? 'NOT_FOUND' : 'ASSISTANT_UNAVAILABLE' }]);
  });
  it('rejects model-supplied client scope and invalid ranges without reading', async () => {
    const client = await connect();
    expect((await client.callTool({ name: 'get_client_card', arguments: { kcr_code: 'other' } })).isError).toBe(true);
    expect((await client.callTool({ name: 'query_clients', arguments: { criteria: { min_unsubmitted_percent: 80, max_unsubmitted_percent: 20 } } })).isError).toBe(true);
    expect(calls).toHaveLength(0);
  });
  it('does not cache client facts across calls', async () => {
    const client = await connect();
    await client.callTool({ name: 'get_client_card', arguments: {} });
    fault = 'missing';
    expect((await client.callTool({ name: 'get_client_card', arguments: {} })).isError).toBe(true);
  });
  it('requires an explicitly configured model and validates endpoint schemes', () => {
    expect(() => createModel({})).toThrow('ASSISTANT_UNAVAILABLE');
    expect(createModel({ baseURL: 'https://example.com/v1', apiKey: 'test', model: 'chosen' }).modelId).toBe('chosen');
    expect(() => parseConfig({ CLIENTS_ENDPOINT: 'file:///tmp/data' })).toThrow();
    expect(() => parseConfig({ ASSISTANT_PROVIDER_BASE_URL: 'https://user:secret@example.com' })).toThrow();
  });
});
