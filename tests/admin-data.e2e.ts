import { describe, test as base } from '@e2e-dev/web';
import { expect } from 'e2e';
import { randomUUID } from 'node:crypto';
import {
  accounts, api, applyClient, businessDate, cleanupClient, dateInQuarter, internal, newCode,
  prepareOperators, publish, quarterAt, requireStatus, signIn, take, workbook, type Actor,
} from './kontur-fixtures';

const test = base.extend<{ data: { admin: Actor; code: string; quarter: string; previous: string } }>({
  data: async ({}, use) => {
    const admin = await signIn(accounts.admin);
    const code = newCode();
    const today = businessDate();
    try {
      await use({ admin, code, quarter: quarterAt(today), previous: quarterAt(today, -1) });
    } finally {
      cleanupClient(admin, code);
    }
  },
});

const registry = (code: string) => workbook(`${code}-registry.xlsx`, {
  'Реестр': [['Код КЦР', 'Название организации', 'Телефон'], [code, `Синтетический реестр ${code}`, '+70000000001']],
});

const batchReport = async (admin: Actor, id: string) =>
  requireStatus(await api(`/imports/${id}`, { actor: admin }), 200, 'Read import report');

async function completed(admin: Actor, id: string) {
  await expect.poll(async () => (await batchReport(admin, id)).status, { timeout: 30_000 }).toBe('COMPLETED');
  return batchReport(admin, id);
}

async function submit(admin: Actor, file: ReturnType<typeof workbook>, quarter: string,
                      type: 'REGISTRY' | 'SNAPSHOT', date: string) {
  const form = new FormData();
  form.set('file', new Blob([new Uint8Array(file.bytes)]), file.path.split(/[\\/]/).at(-1));
  form.set('selected_quarter', quarter);
  form.set('import_type', type);
  form.set('snapshot_date', date);
  const reply = await api('/imports', { actor: admin, method: 'POST', body: form });
  if (![200, 201, 202].includes(reply.status)) requireStatus(reply, 202, 'Submit workbook');
  return completed(admin, reply.body.id);
}

const history = async (admin: Actor, code: string) =>
  requireStatus(await api(`/clients/${code}/snapshots/history?limit=20&offset=0`, { actor: admin }), 200, 'Read snapshot history');

const card = async (admin: Actor, code: string) =>
  requireStatus(await api(`/clients/${code}`, { actor: admin }), 200, 'Read client card');

describe('administrator imports and recovery', { session: 'admin', timeout: 100_000 }, () => {
  test('loads a registry workbook through the browser and reports its accepted row', async ({ app, browser, screen, data }) => {
    const { code, admin, quarter } = data;
    const date = dateInQuarter(quarter, 2);
    const file = registry(code);
    try {
      await app.open('/upload');
      await screen.getByLabel('Файл реестра .xlsx').setInputFiles(file.path);
      await screen.getByRole('textbox', 'Квартал').fill(quarter);
      await screen.getByLabel('Дата среза').fill(date);

      await screen.getByRole('button', 'Загрузить').tap();

      await expect(browser).toHaveURL(/\/import-report\?batch=/);
      await expect(screen.getByText(`${quarter} · ${date} · COMPLETED`, { exact: true })).toBeVisible({ timeout: 30_000 });
      await expect(browser.locator('.stat-card').filter({ hasText: 'Принято' })).toContainText('1');
      expect((await card(admin, code)).organization_name).toBe(`Синтетический реестр ${code}`);
    } finally {
      file.remove();
    }
  });

  test('applies valid snapshot rows and reports duplicate, unknown and annual rows', async ({ data }) => {
    const { code, admin, quarter } = data;
    const annualCode = newCode();
    const annualSheet = `весь ${quarter.slice(0, 4)} год`;
    const headings = ['Код КЦР', 'Всего', 'Непередано', 'Исходный квартал'];
    const file = workbook(`${code}-snapshot.xlsx`, {
      [quarter]: [headings, [code, 100, 10, quarter], [code, 100, 8, quarter], [newCode(), 100, 4, quarter]],
      [annualSheet]: [headings, [code, 100, 99, quarter], [annualCode, 100, 70, quarter]],
    });
    try {
      const registryRow = { quarter, registry: true, date: dateInQuarter(quarter) };
      applyClient(admin, { ...registryRow, code });
      applyClient(admin, { ...registryRow, code: annualCode });
      const report = await submit(admin, file, quarter, 'SNAPSHOT', dateInQuarter(quarter, 2));

      expect(report.total_rows).toBe(3);
      expect(report.success_count).toBe(1);
      expect(report.error_count).toBe(3);
      expect(report.error_log).toEqual(expect.arrayContaining([
        expect.objectContaining({ sheet: quarter, reason: 'Дубликат строки в листе' }),
        expect.objectContaining({ sheet: quarter, reason: expect.stringContaining('Код КЦР отсутствует в реестре') }),
        expect.objectContaining({
          sheet: annualSheet, row_number: 0, reason: 'Годовой, составной или служебный лист пропущен',
        }),
      ]));
      expect((await card(admin, code)).snapshots).toMatchObject([{ quarter, unsubmitted_documents: 10 }]);
      expect((await card(admin, annualCode)).snapshots).toEqual([]);
    } finally {
      file.remove();
      cleanupClient(admin, annualCode);
    }
  });

  test('preserves the current snapshot and total after a backdated observation and replay', async ({ app, screen, data }) => {
    const { code, admin, quarter } = data;
    applyClient(admin, { code, quarter, registry: true, date: dateInQuarter(quarter) });
    const older = { code, quarter, remaining: 12, date: dateInQuarter(quarter, 3) };
    const newer = { code, quarter, remaining: 7, date: dateInQuarter(quarter, 5), omitTotal: true, batch: randomUUID() };
    applyClient(admin, older);
    applyClient(admin, newer);

    applyClient(admin, { ...older, date: dateInQuarter(quarter, 2), remaining: 14 });
    applyClient(admin, newer);
    await app.open(`/client/${code}`);

    await expect(screen.getByText(`${quarter} · 7 из 100 · ${newer.date}`, { exact: true })).toBeVisible();
    expect((await history(admin, code)).items.map((item: { unsubmitted_documents: number }) => item.unsubmitted_documents))
      .toEqual([7, 12, 14]);
  });

  test('marks inherited contact data and clears the warning when a new phone is supplied', async ({ app, screen, data }) => {
    const { code, admin, quarter, previous } = data;
    applyClient(admin, { code, quarter: previous, registry: true, phone: '+70000000001', date: dateInQuarter(previous) });
    applyClient(admin, { code, quarter, remaining: 9, phone: null, date: dateInQuarter(quarter) });
    await app.open(`/client/${code}`);
    await expect(screen.getByRole('heading', 'Требует проверки')).toBeVisible();
    const inherited = await card(admin, code);
    expect(inherited.contacts.phone).toBe('+70000000001');
    expect(Object.keys(inherited.inherited_fields ?? {})).toContain('contacts');

    applyClient(admin, { code, quarter, remaining: 8, phone: '+70000000002', date: dateInQuarter(quarter, 2) });
    await screen.getByRole('button', 'Обновить карточку').tap();

    await expect(screen.getByText(/\+70000000002/)).toBeVisible();
    const current = await card(admin, code);
    expect(current.contacts.phone).toBe('+70000000002');
    expect(Object.keys(current.inherited_fields ?? {})).not.toContain('contacts');
  });

  test('rejects the earlier rollback and removes work created by the latest snapshot', async ({ app, browser, screen, data }) => {
    const { code, admin, quarter } = data;
    const first = registry(code);
    const last = workbook(`${code}-rollback.xlsx`, {
      [quarter]: [['Код КЦР', 'Всего', 'Непередано'], [code, 100, 7]],
    });
    try {
      const before = await submit(admin, first, quarter, 'REGISTRY', dateInQuarter(quarter, 2));
      const applied = await submit(admin, last, quarter, 'SNAPSHOT', dateInQuarter(quarter, 3));
      const tooOld = await api(`/imports/${before.id}/rollback`, {
        actor: admin, method: 'POST', body: { operation_id: randomUUID() },
      });
      expect(tooOld.status).toBe(409);
      expect((await card(admin, code)).snapshots).toMatchObject([{ quarter, unsubmitted_documents: 7 }]);
      const workPath = `/clients/${code}/cycles`;
      const cycles = requireStatus(internal('operator-work', workPath, admin), 200, 'Read imported work cycle');
      expect(cycles.items).toMatchObject([{ work_quarter: quarter, source_import_batch_id: applied.id }]);
      const { first: operator } = await prepareOperators();
      await publish(admin, code, quarter);
      requireStatus(await take(operator, code, quarter), 200, 'Start operator work on the latest snapshot');
      const historyNote = requireStatus(await api(`/clients/${code}/events`, {
        actor: operator, method: 'POST', body: {
          request_id: randomUUID(), event_type: 'HISTORICAL_COMMENT', comment_text: 'Общая история',
          historical_event_date: `${businessDate()}T00:00:00+03:00`, document_quarters: [],
        },
      }), 201, 'Add a historical comment outside the imported cycle');
      await app.open(`/import-report?batch=${applied.id}`);
      await browser.onDialog('accept');

      await screen.getByRole('button', 'Откатить пакет').tap();

      await expect.poll(async () => (await batchReport(admin, applied.id)).status, { timeout: 20_000 }).toBe('ROLLED_BACK');
      const restored = await card(admin, code);
      expect(restored.organization_name).toBe(`Синтетический реестр ${code}`);
      expect(restored.snapshots).toHaveLength(0);
      expect(requireStatus(internal('operator-work', workPath, admin), 200, 'Read work after rollback').items).toHaveLength(0);
      const events = requireStatus(internal('operator-work', `/clients/${code}/events`, admin), 200, 'Read history after rollback');
      expect(events.items.map((event: { id: string }) => event.id)).toEqual([historyNote.id]);
    } finally {
      last.remove();
      first.remove();
    }
  });

  test('leaves a client and both quarters intact when deletion is cancelled', async ({ app, browser, screen, data }) => {
    const { code, admin, quarter, previous } = data;
    applyClient(admin, { code, quarter: previous, registry: true, date: dateInQuarter(previous) });
    applyClient(admin, { code, quarter: previous, remaining: 4, date: dateInQuarter(previous, 2) });
    applyClient(admin, { code, quarter, remaining: 7, date: dateInQuarter(quarter) });
    await app.open(`/client/${code}`);
    await browser.onDialog('dismiss');

    await screen.getByRole('button', 'Удалить клиента').tap();

    expect((await card(admin, code)).snapshots).toHaveLength(2);
    await expect(screen.getByRole('button', 'Удалить квартал')).toHaveCount(2);
  });

  for (const scope of ['quarter', 'client']) {
    test(`deletes just the selected ${scope} and preserves unrelated work`, async ({ app, browser, screen, data }) => {
      const { admin, code, quarter, previous } = data;
      const { first } = await prepareOperators();
      applyClient(admin, { code, quarter: previous, registry: true, date: dateInQuarter(previous) });
      applyClient(admin, { code, quarter: previous, remaining: 4, date: dateInQuarter(previous, 2) });
      applyClient(admin, { code, quarter, remaining: 7, date: dateInQuarter(quarter) });
      await publish(admin, code, quarter);
      requireStatus(await take(first, code, quarter), 200, 'Prepare operator assignment');
      const memo = 'Общий комментарий по двум кварталам';
      const removedMemo = 'Комментарий только по удаляемому кварталу';
      for (const [message, quarters] of [[memo, [previous, quarter]], [removedMemo, [previous]]] as const) {
        requireStatus(await api(`/clients/${code}/events`, { actor: first, method: 'POST', body: {
          request_id: randomUUID(), event_type: 'HISTORICAL_COMMENT', document_quarters: quarters,
          comment_text: message, historical_event_date: new Date(Date.now() - 86_400_000).toISOString(),
        } }), 201, 'Prepare operator history');
      }
      await app.open(`/client/${code}`);
      await browser.onDialog('accept');

      if (scope === 'quarter') {
        await browser.locator('.status-row').filter({ hasText: new RegExp(`^${previous} ·`) })
          .getByRole('button', 'Удалить квартал').tap();
        await expect(screen.getByText('Данные квартала удалены', { exact: true })).toBeVisible();
        expect((await card(admin, code)).snapshots.map((s: { quarter: string }) => s.quarter)).toEqual([quarter]);
        await expect(screen.getByText(new RegExp(memo))).toBeVisible();
        await expect(screen.getByText(new RegExp(removedMemo))).toHaveCount(0);
        const work = requireStatus(internal('operator-work', `/clients/${code}/cycles`, admin), 200, 'Verify retained current cycle');
        expect(work.items).toMatchObject([{ work_quarter: quarter, assigned_operator_id: first.user.id }]);
      } else {
        await screen.getByRole('button', 'Удалить клиента').tap();
        await expect(browser).toHaveURL('/');
        expect((await api(`/clients/${code}`, { actor: admin })).status).toBe(404);
        const work = requireStatus(internal('operator-work', `/clients/${code}/cycles`, admin), 200, 'Verify deletion of working cycles');
        expect(work.items).toHaveLength(0);
      }
    });
  }
});
