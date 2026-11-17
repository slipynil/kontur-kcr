import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

try {
  process.loadEnvFile();
} catch (error) {
  if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
}

type Role = 'ADMIN' | 'OPERATOR';
type Account = { login: string; password: string; role: Role };
export type Actor = { token: string; user: { id: string; login: string; role: Role } };
export type Reply = { status: number; body: any };

export const accounts = {
  admin: {
    login: process.env.E2E_USER_ADMIN_USERNAME ?? 'admin',
    password: process.env.E2E_USER_ADMIN_PASSWORD ?? process.env.E2E_ADMIN_PASSWORD ?? 'password123',
    role: 'ADMIN',
  },
  operator_a: {
    login: process.env.E2E_USER_OPERATOR_A_USERNAME ?? 'kontur-e2e-operator-a',
    password: process.env.E2E_USER_OPERATOR_A_PASSWORD ?? 'kontur-e2e-local-A123',
    role: 'OPERATOR',
  },
  operator_b: {
    login: process.env.E2E_USER_OPERATOR_B_USERNAME ?? 'kontur-e2e-operator-b',
    password: process.env.E2E_USER_OPERATOR_B_PASSWORD ?? 'kontur-e2e-local-B123',
    role: 'OPERATOR',
  },
} satisfies Record<string, Account>;

export const appUrl = process.env.APP_URL ?? 'http://localhost:80';

export async function api(path: string, options: {
  actor?: Actor; method?: string; body?: object | FormData;
} = {}): Promise<Reply> {
  const headers = new Headers();
  if (options.actor) headers.set('Authorization', `Bearer ${options.actor.token}`);
  const multipart = options.body instanceof FormData;
  if (options.body && !multipart) headers.set('Content-Type', 'application/json');
  const response = await fetch(new URL(`/api/v1${path}`, appUrl), {
    method: options.method ?? 'GET',
    headers,
    body: multipart ? options.body as FormData : JSON.stringify(options.body),
    signal: AbortSignal.timeout(15_000),
  });
  const raw = await response.text();
  let body;
  try { body = raw ? JSON.parse(raw) : null; } catch { body = null; }
  return { status: response.status, body };
}

export function requireStatus(reply: Reply, status: number, step: string) {
  if (reply.status !== status) {
    throw new Error(`${step}: expected HTTP ${status}, got ${reply.status}${reply.body?.code ? ` (${reply.body.code})` : ''}`);
  }
  return reply.body;
}

export async function signIn(account: Account): Promise<Actor> {
  const session = requireStatus(await api('/auth/login', {
    method: 'POST', body: { login: account.login, password: account.password },
  }), 200, `Sign in as ${account.login}`) as Actor;
  if (session.user.role !== account.role) throw new Error(`Unexpected role for ${account.login}`);
  return session;
}

export async function prepareOperators() {
  const admin = await signIn(accounts.admin);
  const provision = async (account: Account) => {
    const created = await api('/users', { method: 'POST', actor: admin, body: account });
    if (created.status !== 409) requireStatus(created, 201, `Create ${account.login}`);
    return signIn(account);
  };
  return { admin, first: await provision(accounts.operator_a), second: await provision(accounts.operator_b) };
}

type Service = 'clients' | 'operator-work' | 'import-api';
function containerPython(service: Service, script: string, args: unknown) {
  const project = process.env.E2E_COMPOSE_PROJECT ?? process.env.COMPOSE_PROJECT_NAME ?? 'kontur-kcr';
  const container = execFileSync('docker', [
    'ps', '-q', '--filter', `label=com.docker.compose.project=${project}`,
    '--filter', `label=com.docker.compose.service=${service}`,
  ], { encoding: 'utf8', timeout: 10_000 }).trim();
  if (!container || container.includes('\n')) throw new Error(`Expected one running ${project}/${service} container`);
  return execFileSync('docker', ['exec', '-i', container, 'python', '-c', script], {
    input: JSON.stringify(args), timeout: 20_000, maxBuffer: 1024 * 1024, windowsHide: true,
  });
}

const internalRequest = [
  'import json, sys, urllib.request, urllib.error',
  'port, path, method, payload, headers = json.load(sys.stdin)',
  'data = json.dumps(payload).encode() if payload is not None else None',
  'request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data, headers, method=method)',
  'try:',
  '    result = urllib.request.urlopen(request, timeout=12)',
  'except urllib.error.HTTPError as error:',
  '    result = error',
  'with result:',
  '    raw = result.read()',
  '    print(json.dumps(dict(status=result.code, body=json.loads(raw) if raw else None)))',
].join('\n');

export function internal(service: 'clients' | 'operator-work', path: string, actor: Actor,
                         method = 'GET', body: object | null = null): Reply {
  return JSON.parse(containerPython(service, internalRequest, [
    8000, `/internal/v1${path}`, method, body, {
      'Content-Type': 'application/json',
      'X-User-Id': actor.user.id,
      'X-User-Role': actor.user.role,
    },
  ]).toString('utf8'));
}

export const newCode = () => `E2E-${randomUUID().slice(0, 12)}`;
export function quarterAt(date: string, offset = 0) {
  const [year, month] = date.split('-').map(Number);
  const period = year * 4 + Math.floor((month - 1) / 3) + offset;
  return `${Math.floor(period / 4)}_Q${period % 4 + 1}`;
}

export const businessDate = () => new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Europe/Moscow', year: 'numeric', month: '2-digit', day: '2-digit',
}).format(new Date());
export const addDays = (date: string, days: number) =>
  new Date(Date.parse(date) + days * 86_400_000).toISOString().slice(0, 10);

export function dateInQuarter(quarter: string, day = 1) {
  const [year, number] = quarter.split('_Q');
  return `${year}-${String((Number(number) - 1) * 3 + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

export function applyClient(admin: Actor, options: {
  code: string; quarter: string; remaining?: number; date?: string;
  registry?: boolean; phone?: string | null; omitTotal?: boolean; inn?: string; batch?: string;
}) {
  const { code, quarter, remaining, date = businessDate(), registry, phone, omitTotal, inn, batch = randomUUID() } = options;
  const client = {
    kcr_code: code,
    ...(registry ? { organization_name: `Тестовый клиент ${code}`, inn, tags: ['VIP'] } : {}),
    ...(phone === undefined ? {} : { contacts: phone === null ? null : { phone } }),
  };
  const applied = requireStatus(internal('clients', '/import/apply', admin, 'POST', {
    batch_id: batch, uploaded_by: admin.user.id, selected_quarter: quarter, snapshot_date: date,
    import_type: registry ? 'REGISTRY' : 'SNAPSHOT',
    items: [{ client, ...(remaining === undefined ? {} : {
      snapshot: { quarter, unsubmitted_documents: remaining, ...(!omitTotal ? { total_documents: 100 } : {}) },
    }) }],
  }), 200, `Apply fixture ${code}/${quarter}`);
  if (applied.accepted_rows !== 1) {
    throw new Error(`Fixture row rejected for ${code}/${quarter}: ${JSON.stringify(applied.errors)}`);
  }
  return applied;
}

export function applyWork(admin: Actor, code: string, quarter: string, remaining: number) {
  requireStatus(internal('operator-work', `/cycles/apply-snapshot?work_quarter=${quarter}`, admin, 'PUT', {
    batch_id: randomUUID(), clients: [{ kcr_code: code, outstanding_documents: remaining }],
  }), 204, 'Prepare work cycle');
}

export const publishReply = (admin: Actor, codes: string[], quarter: string) =>
  api(`/pool/publication?work_quarter=${quarter}`, { actor: admin, method: 'PUT', body: { codes } });

export async function publish(admin: Actor, code: string, quarter: string) {
  requireStatus(await publishReply(admin, [code], quarter), 204, 'Publish test client');
}

export function assignment(actor: Actor, code: string, quarter: string) {
  return requireStatus(internal('operator-work', `/clients/${code}/assignment?work_quarter=${quarter}`, actor), 200, 'Read assignment');
}

export const take = (actor: Actor, code: string, quarter: string, expectedId: string | null = null, requestId = randomUUID()) =>
  api(`/clients/${code}/take?work_quarter=${quarter}`, {
    actor, method: 'POST', body: { expected_assignment_id: expectedId, request_id: requestId },
  });

export function cleanupClient(admin: Actor, code: string) {
  if (!/^E2E-[a-f0-9-]{12}$/.test(code)) throw new Error('Can only clean up a generated E2E client');
  requireStatus(internal('operator-work', '/data-cleanup', admin, 'POST', {
    operation_id: randomUUID(), kcr_code: code, scope: 'CLIENT',
  }), 200, 'Clean up operator fixture');
  requireStatus(internal('clients', `/clients/${code}`, admin, 'DELETE', {
    operation_id: randomUUID(), requested_by: admin.user.id,
  }), 200, 'Clean up client fixture');
}

export function expireWork(code: string, quarter: string, kind: 'reassignment' | 'wait') {
  if (!/^E2E-[a-f0-9-]{12}$/.test(code)) throw new Error('Can only advance a generated E2E client');
  const clock = [
    'import json, os, sys',
    'from sqlalchemy import create_engine, text',
    'code, quarter, kind = json.load(sys.stdin)',
    'sql = {"reassignment": "UPDATE client_reassignment_requests SET created_at=now() - interval \'2 hours\', expires_at=now() - interval \'1 hour\' WHERE kcr_code=:code AND work_quarter=:quarter AND status=\'PENDING\'",',
    '       "wait": "UPDATE client_work_cycles SET wait_until=current_date - 1 WHERE kcr_code=:code AND work_quarter=:quarter AND wait_until IS NOT NULL"}',
    'with create_engine(os.environ["OPERATOR_WORK_DATABASE_URL"]).begin() as db:',
    '    changed = db.execute(text(sql[kind]), dict(code=code, quarter=quarter)).rowcount',
    '    if changed != 1: raise RuntimeError(f"Expected one test row, got {changed}")',
  ].join('\n');
  containerPython('operator-work', clock, [code, quarter, kind]);
}

export function workbook(name: string, sheets: Record<string, (string | number | null)[][]>) {
  const writer = [
    'import json, sys',
    'from io import BytesIO',
    'from openpyxl import Workbook',
    'wb = Workbook()',
    'wb.remove(wb.active)',
    'for title, rows in json.load(sys.stdin).items():',
    '    ws = wb.create_sheet(title)',
    '    for row in rows: ws.append(row)',
    'result = BytesIO()',
    'wb.save(result)',
    'sys.stdout.buffer.write(result.getvalue())',
  ].join('\n');
  const bytes = containerPython('import-api', writer, sheets);
  mkdirSync('.e2e', { recursive: true });
  const directory = mkdtempSync(join('.e2e', 'kontur-fixture-'));
  const path = join(directory, name);
  writeFileSync(path, bytes);
  return { path, bytes, remove: () => rmSync(directory, { recursive: true, force: true }) };
}
