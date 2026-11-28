import * as z from 'zod/v4';
import { ReadApi, ReadError } from './upstreams.js';

export const quarter = z.string().regex(/^(?!0000)[0-9]{4}_Q[1-4]$/);
const count = z.number().int().min(0);
const tag = z.enum(['VIP', 'NOVICE', 'INTEGRATOR', 'TECH_PARTNER', 'MPP', 'DO_NOT_WORK']);
export const criteriaSchema = z.object({
  quarter: quarter.optional(), work_quarter: quarter.optional(),
  dynamics: z.enum(['REDUCING', 'NO_CHANGE', 'GROWING', 'ALL_SUBMITTED']).optional(),
  duration: z.enum(['FIRST_TIME', 'REPEATED', 'LONG_DELAY_2Q']).optional(),
  tags: z.array(tag).optional(), exclude_tags: z.array(tag).optional(),
  assignment: z.enum(['FREE', 'ASSIGNED']).optional(),
  min_unsubmitted_documents: count.max(2147483647).optional(),
  max_unsubmitted_documents: count.max(2147483647).optional(),
  min_unsubmitted_percent: z.number().min(0).max(100).optional(),
  max_unsubmitted_percent: z.number().min(0).max(100).optional(),
}).strict().refine(value =>
  (value.min_unsubmitted_documents ?? 0) <= (value.max_unsubmitted_documents ?? Infinity) &&
  (value.min_unsubmitted_percent ?? 0) <= (value.max_unsubmitted_percent ?? Infinity), 'Invalid range');
export type Criteria = z.infer<typeof criteriaSchema>;
export const pageInput = z.object({ page: z.number().int().min(1).max(1000000).default(1),
  page_size: z.number().int().min(1).max(100).default(20), quarter: quarter.optional() }).strict();
const snapshot = z.object({ quarter, total_documents: count, unsubmitted_documents: count, snapshot_date: z.string() });
const row = z.object({ kcr_code: z.string(), tags: z.array(z.string()),
  dynamics: z.string().nullable().optional(), duration: z.string().nullable().optional(),
  quarter_summary: z.object({ unsubmitted_documents: count, total_documents: count,
    unsubmitted_percent: z.number().nullable() }).nullable(),
  outstanding_documents: count.optional(),
  dynamics_quarter: quarter.optional(), dynamics_previous_quarter: quarter.optional(),
  dynamics_current_documents: count.nullable().optional(), dynamics_previous_documents: count.nullable().optional(),
});
const card = row.extend({ snapshots: z.array(snapshot), needs_review: z.boolean().optional(),
  organization_name: z.string().nullable().optional() });
const historyRecord = z.object({ id: z.uuid(), kcr_code: z.string(), quarter,
  unsubmitted_documents: count, snapshot_date: z.string(), created_at: z.string(),
  observation_order: count.optional(), observation_quarter: quarter.optional() });
const event = z.object({ id: z.uuid(), kcr_code: z.string(), event_type: z.string(),
  occurred_at: z.string(), work_quarter: quarter.nullable().optional(),
  document_quarters: z.array(quarter).optional(), payload: z.record(z.string(), z.unknown()),
});
const workState = z.object({ kcr_code: z.string(), work_quarter: quarter,
  stage: z.string().nullable().optional(), assigned_operator_id: z.uuid().nullable(),
  wait_until: z.string().nullable().optional(), awaiting_balance: z.boolean(),
});
function query(params: Record<string, unknown>): string {
  const result = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) result.set(key, Array.isArray(value) ? value.join(',') : String(value));
  }
  return `?${result}`;
}

/** The client scope is injected by the host; it is never a model argument. */
export class ReadTools {
  private readonly api: ReadApi;
  private readonly kcrCode?: string;
  constructor(api: ReadApi, kcrCode?: string) { this.api = api; this.kcrCode = kcrCode; }

  private clientPath(): string {
    if (!this.kcrCode) throw new ReadError('FORBIDDEN');
    return `/clients/${encodeURIComponent(this.kcrCode)}`;
  }

  async getClientCard() {
    const path = this.clientPath();
    const client = await this.api.get('clients', path, card);
    let work = null;
    try { work = await this.api.get('operatorWork', `${path}/work-state`, workState); }
    catch (error) { if (!(error instanceof ReadError) || error.code !== 'NOT_FOUND') throw error; }
    if (client.kcr_code !== this.kcrCode || (work && work.kcr_code !== this.kcrCode)) throw new ReadError('ASSISTANT_UNAVAILABLE');
    return { client, work };
  }

  async getSnapshotHistory(input: z.infer<typeof pageInput>) {
    const result = await this.api.get('clients', `${this.clientPath()}/snapshots/history${query(input)}`,
      z.object({ items: z.array(historyRecord), total: count, limit: count, offset: count }));
    if (result.items.some(item => item.kcr_code !== this.kcrCode)) throw new ReadError('ASSISTANT_UNAVAILABLE');
    return result;
  }

  async getHistoryEvents(input: z.infer<typeof pageInput>) {
    const { quarter: work_quarter, ...paging } = input;
    const result = await this.api.get('clientHistory', `${this.clientPath()}/events${query({ ...paging, work_quarter })}`,
      z.object({ items: z.array(event), total: count, page: count, page_size: count }));
    if (result.items.some(item => item.kcr_code !== this.kcrCode)) throw new ReadError('ASSISTANT_UNAVAILABLE');
    return result;
  }

  async getCallContext() {
    const [state, history] = await Promise.all([
      this.getClientCard(), this.getHistoryEvents({ page: 1, page_size: 100 }),
    ]);
    const calls = history.items.filter(item => item.event_type === 'CALL_RESULT');
    // Only claim an exact call count when the complete feed has been read.
    const complete = history.total === history.items.length;
    const periods = await Promise.all(state.client.snapshots.map(async item => {
      const observations = await this.getSnapshotHistory({ quarter: item.quarter, page: 1, page_size: 2 });
      const [latest, previous] = observations.items;
      return { ...item, latest_observation: latest ?? null, previous_observation: previous ?? null,
        change: latest && previous ? latest.unsubmitted_documents - previous.unsubmitted_documents : null };
    }));
    return { ...state, periods, recent_events: history.items, history_complete: complete,
      next_call_number: complete ? calls.length + 1 : null,
      last_call: calls[0] ?? null,
      // Promise fulfilment requires dated observations; do not infer it from current balance.
      promise_status: 'REQUIRES_OBSERVATION_COMPARISON',
    };
  }

  async queryClients(criteria: Criteria, limit: number) {
    if (this.api.identity.role !== 'ADMIN' || this.kcrCode) throw new ReadError('FORBIDDEN');
    const { exclude_tags, assignment, work_quarter, ...filters } = criteriaSchema.parse(criteria);
    // current-assignees reads the current published cycle, never a historical quarter.
    const not_applied = work_quarter ? ['work_quarter: отбор доступен только по текущим опубликованным циклам'] : [];
    const applied = { ...filters, ...(exclude_tags ? { exclude_tags } : {}), ...(assignment ? { assignment } : {}) };
    const items: z.infer<typeof row>[] = [];
    let total = 0;
    for (let page = 1; ; page++) {
      const result = await this.api.get('clients', `/clients${query({ ...filters, page, page_size: 100 })}`,
        z.object({ items: z.array(row), total: count, page: count, page_size: count }));
      const assignments = assignment && result.items.length
        ? new Set((await this.api.assignees(result.items.map(item => item.kcr_code))).items.map(item => item.kcr_code)) : null;
      for (const item of result.items) {
        if (exclude_tags?.some(value => item.tags.includes(value))) continue;
        if (assignments && assignments.has(item.kcr_code) !== (assignment === 'ASSIGNED')) continue;
        total++;
        if (items.length < limit) items.push(item);
      }
      if (page * 100 >= result.total) break;
      if (!result.items.length || page >= 1000000) throw new ReadError('ASSISTANT_UNAVAILABLE');
    }
    return { items, total, criteria: applied, not_applied };
  }
}
