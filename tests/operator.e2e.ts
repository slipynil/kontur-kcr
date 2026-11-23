import { describe, test as base } from '@e2e-dev/web';
import { expect } from 'e2e';
import { randomUUID } from 'node:crypto';
import {
  addDays, api, applyClient, applyWork, assignment, businessDate, cleanupClient, dateInQuarter,
  expireWork, internal, newCode, prepareOperators, publish, publishReply, quarterAt, requireStatus, take,
  type Actor,
} from './kontur-fixtures';

const test = base.extend<{
  work: { admin: Actor; first: Actor; second: Actor; code: string; quarter: string; today: string };
}>({
  work: async ({}, use) => {
    const { admin, first, second } = await prepareOperators();
    const code = newCode();
    const today = businessDate();
    const quarter = quarterAt(today);
    try {
      applyClient(admin, { code, quarter, registry: true, date: dateInQuarter(quarter) });
      applyClient(admin, { code, quarter, remaining: 7, date: dateInQuarter(quarter, 2) });
      applyWork(admin, code, quarter, 7);
      await use({ admin, first, second, code, quarter, today });
    } finally {
      cleanupClient(admin, code);
    }
  },
});

const postEvent = (actor: Actor, code: string, body: object) =>
  api(`/clients/${code}/events`, { actor, method: 'POST', body });

const call = (quarter: string, assignmentId: string, values = {}) => ({
  request_id: randomUUID(), event_type: 'CALL_RESULT', expected_assignment_id: assignmentId,
  work_quarter: quarter, call_result: 'AGREEMENT', comment_text: 'Тестовая договорённость',
  document_quarters: [quarter], ...values,
});

describe('operator work', { session: 'operator_a', timeout: 75_000 }, () => {
  test('only allows taking a client after publication and preserves the assignment on republication', async ({ work }) => {
    const { admin, first, code, quarter } = work;
    const unpublished = await take(first, code, quarter);
    expect(unpublished.status).toBe(404);
    expect(unpublished.body.code).toBe('NOT_FOUND');

    const denied = await publishReply(first, [code], quarter);
    expect(denied.status).toBe(403);
    await publish(admin, code, quarter);
    const result = requireStatus(await take(first, code, quarter), 200, 'Take published client');
    await publish(admin, code, quarter);

    expect(assignment(first, code, quarter)).toMatchObject({
      assignment_id: result.assignment_id, assigned_operator_id: first.user.id, stage: 'IN_WORK',
    });
  });

  test('assigns a published client from the browser card', async ({ app, screen, work }) => {
    const { admin, first, code, quarter } = work;
    await publish(admin, code, quarter);
    await app.open(`/client/${code}`);
    const takeButton = screen.getByRole('button', 'Взять в работу');
    await expect(takeButton).toBeEnabled({ timeout: 5000 });

    await takeButton.tap();

    await expect(screen.getByText('Клиент закреплён за вами', { exact: true })).toBeVisible();
    expect(assignment(first, code, quarter).assigned_operator_id).toBe(first.user.id);
  });

  test('allows one winner when two operators take the same free client', async ({ work }) => {
    const { admin, first, second, code, quarter } = work;
    await publish(admin, code, quarter);

    const results = await Promise.all([take(first, code, quarter), take(second, code, quarter)]);

    expect(results.map(result => result.status).sort()).toEqual([200, 409]);
    expect(assignment(first, code, quarter).assigned_operator_id).toBe(
      results.find(result => result.status === 200)?.body.assigned_operator_id,
    );
  });

  test('immediately takes over a client assigned to another operator', { session: 'operator_b' }, async ({ app, screen, work }) => {
    const { admin, first, second, code, quarter } = work;
    await publish(admin, code, quarter);
    requireStatus(await take(first, code, quarter), 200, 'Prepare original assignment');
    await app.open(`/client/${code}`);

    await expect(screen.getByText(first.user.login, { exact: true })).toBeVisible();
    await screen.getByRole('button', 'Перезакрепить на себя').tap();
    await expect(screen.getByText('Клиент перезакреплён на вас', { exact: true })).toBeVisible();
    await expect(screen.getByRole('button', 'Записать звонок')).toBeVisible();
    expect(assignment(second, code, quarter).assigned_operator_id).toBe(second.user.id);
    const notifications = requireStatus(
      await api('/reassignment-notifications', { actor: second }), 200, 'Read existing requests',
    );
    expect(notifications.items.some((item: { kcr_code: string }) => item.kcr_code === code)).toBe(false);

    await screen.getByRole('tab', 'Лента событий').tap();
    await expect(screen.getByRole('listitem').filter({ hasText: /взят в работу/i })).toContainText(first.user.login);
    await expect(screen.getByRole('listitem').filter({ hasText: /перезакрепл[её]н/i })).toContainText(second.user.login);
  });

  test('keeps an old-form call without its wait after immediate reassignment', { session: 'operator_b' },
    async ({ app, screen, work }) => {
      const { admin, first, second, code, quarter, today } = work;
      await publish(admin, code, quarter);
      const original = requireStatus(await take(first, code, quarter), 200, 'Prepare original assignment');
      requireStatus(await take(second, code, quarter, original.assignment_id), 200, 'Immediately reassign');
      const recorded = requireStatus(await postEvent(first, code, call(quarter, original.assignment_id, {
        wait_until: addDays(today, 4),
      })), 201, 'Save stale-form call');
      await app.open(`/client/${code}`);

      expect(recorded.wait_applied).toBe(false);
      expect(assignment(second, code, quarter)).toMatchObject({ assigned_operator_id: second.user.id, wait_until: null });
      await screen.getByRole('tab', 'Лента событий').tap();
      await expect(screen.getByText(/Тестовая договорённость/)).toBeVisible();
    });

  for (const result of ['AGREEMENT', 'REFUSAL', 'NO_ANSWER', 'WRONG_NUMBER']) {
    test(`records ${result} once when the same call request is resent`, async ({ app, screen, work }) => {
      const { admin, first, code, quarter, today } = work;
      await publish(admin, code, quarter);
      const original = requireStatus(await take(first, code, quarter), 200, 'Prepare assignment');
      const comment = `Фиксация ${result} ${code}`;
      const promised = result === 'AGREEMENT' ? addDays(today, 5) : null;
      const body = call(quarter, original.assignment_id, {
        call_result: result, comment_text: comment, promised_date: promised,
      });

      const firstSave = requireStatus(await postEvent(first, code, body), 201, 'Save call');
      const replay = await postEvent(first, code, body);
      await app.open(`/client/${code}`);

      expect([200, 201]).toContain(replay.status);
      expect(replay.body.id).toBe(firstSave.id);
      const history = requireStatus(await api(`/clients/${code}/events`, { actor: first }), 200, 'Read call history');
      expect(history.items.filter((event: { id: string }) => event.id === firstSave.id)).toHaveLength(1);
      expect(history.items.find((event: { id: string }) => event.id === firstSave.id)).toMatchObject({
        call_result: result, promised_date: promised, document_quarters: [quarter],
      });
      await screen.getByRole('tab', 'Лента событий').tap();
      await expect(screen.getByText(new RegExp(comment))).toBeVisible();
    });
  }

  test('saves a backdated comment from the card and rejects a future date', async ({ app, screen, work }) => {
    const { admin, first, code, quarter, today } = work;
    await publish(admin, code, quarter);
    await app.open(`/client/${code}`);
    await screen.getByRole('button', 'Добавить событие').tap();
    await screen.getByLabel('Дата комментария').fill(`${addDays(today, -2)}T12:00`);
    await screen.getByLabel('Комментарий', { exact: true }).fill('Тестовый комментарий за прошлую дату');
    await screen.getByRole('checkbox', quarter).check();

    await screen.getByRole('button', 'Сохранить событие').tap();
    const future = await postEvent(first, code, {
      request_id: randomUUID(), event_type: 'HISTORICAL_COMMENT', comment_text: 'Будущая дата',
      historical_event_date: `${addDays(today, 2)}T12:00:00+03:00`, document_quarters: [],
    });

    await expect(screen.getByText(/Тестовый комментарий за прошлую дату/)).toBeVisible();
    expect(future.status).toBe(422);
  });

  test('records a call and a deadline through the operator dialog', async ({ app, screen, work }) => {
    const { admin, first, code, quarter, today } = work;
    await publish(admin, code, quarter);
    requireStatus(await take(first, code, quarter), 200, 'Prepare assignment');
    await app.open(`/client/${code}`);
    await screen.getByRole('button', 'Записать звонок').tap();
    const dialog = screen.getByRole('dialog', 'Результат звонка');
    const waitUntil = addDays(today, 4);
    await dialog.getByLabel('Обещанный срок').fill(waitUntil);
    await dialog.getByLabel('Ожидание до').fill(waitUntil);
    await dialog.getByLabel('Комментарий', { exact: true }).fill('Позвонить после согласованного срока');
    await dialog.getByRole('checkbox', quarter).check();
    await dialog.getByRole('button', 'Сохранить событие').tap();

    await expect(screen.getByText('Событие сохранено', { exact: true })).toBeVisible();
    await expect(screen.getByText('Позвонить после согласованного срока', { exact: true })).toBeVisible();
    expect(assignment(first, code, quarter)).toMatchObject({ wait_until: waitUntil });
  });

  test('carries a live wait across quarters and blocks another operator', async ({ work }) => {
    const { admin, first, second, code, quarter, today } = work;
    const next = quarterAt(today, 1);
    const wait = addDays(dateInQuarter(next), 15);
    await publish(admin, code, quarter);
    const original = requireStatus(await take(first, code, quarter), 200, 'Prepare assignment');
    const saved = requireStatus(await postEvent(first, code, call(quarter, original.assignment_id, {
      promised_date: addDays(wait, 2), wait_until: wait,
    })), 201, 'Save wait');
    expect(saved.wait_applied).toBe(true);

    applyWork(admin, code, next, 7);
    await publish(admin, code, next);
    const carried = assignment(first, code, next);
    const rejected = await take(second, code, next, carried.assignment_id);

    expect(carried).toMatchObject({ stage: 'WAIT_DEADLINE', wait_until: wait, assigned_operator_id: first.user.id });
    expect(rejected.status).toBe(409);
    expect(rejected.body.code).toBe('CLIENT_WAIT_DEADLINE_ACTIVE');
  });

  for (const remaining of [7, 0]) {
    test(`checks the current balance at an expired deadline (${remaining} remaining)`, async ({ work }) => {
      const { admin, first, second, code, quarter, today } = work;
      await publish(admin, code, quarter);
      const original = requireStatus(await take(first, code, quarter), 200, 'Prepare assignment');
      requireStatus(await postEvent(first, code, call(quarter, original.assignment_id, {
        wait_until: addDays(today, 2),
      })), 201, 'Save wait');
      if (!remaining) applyClient(admin, { code, quarter, remaining: 0, date: dateInQuarter(quarter, 3) });

      expireWork(code, quarter, 'wait');
      if (remaining) {
        await expect.poll(() => assignment(first, code, quarter).stage, { timeout: 40_000 }).toBe('POOL');
        expect((await take(second, code, quarter)).status).toBe(200);
      } else {
        const blocked = await take(second, code, quarter, original.assignment_id);
        expect(blocked.status).toBe(409);
        expect(assignment(first, code, quarter).assigned_operator_id).toBe(first.user.id);
        const cycles = requireStatus(internal('operator-work', `/clients/${code}/cycles`, admin), 200, 'Read current work cycle');
        expect(cycles.items.some((cycle: { is_current: boolean; stage: string }) => cycle.is_current && cycle.stage === 'POOL')).toBe(false);
      }
    });
  }

  test('keeps the assignment when Clients cannot confirm the balance at the deadline', async ({ work }) => {
    const { admin, first, second, code, quarter, today } = work;
    await publish(admin, code, quarter);
    const original = requireStatus(await take(first, code, quarter), 200, 'Prepare assignment');
    requireStatus(await postEvent(first, code, call(quarter, original.assignment_id, {
      wait_until: addDays(today, 2),
    })), 201, 'Save wait');
    requireStatus(internal('clients', `/clients/${code}`, admin, 'DELETE', {
      operation_id: randomUUID(), requested_by: admin.user.id,
    }), 200, 'Simulate missing balance for test client');

    expireWork(code, quarter, 'wait');
    const blocked = await take(second, code, quarter, original.assignment_id);

    expect(blocked.status).toBe(409);
    expect(assignment(first, code, quarter)).toMatchObject({
      assigned_operator_id: first.user.id, awaiting_balance: true, stage: 'WAIT_DEADLINE',
    });
  });
});
