import { describe, test as base } from '@e2e-dev/web';
import { expect } from 'e2e';
import { randomInt } from 'node:crypto';
import {
  accounts, api, applyClient, businessDate, cleanupClient, dateInQuarter, newCode, publish, quarterAt, signIn,
  type Actor,
} from './kontur-fixtures';

const test = base.extend<{ client: { admin: Actor; code: string; inn: string; quarter: string; previous: string } }>({
  client: async ({}, use) => {
    const admin = await signIn(accounts.admin);
    const code = newCode();
    const inn = String(randomInt(100_000_000, 999_999_999));
    const today = businessDate();
    const quarter = quarterAt(today);
    const previous = quarterAt(today, -1);
    try {
      applyClient(admin, { code, quarter: previous, date: dateInQuarter(previous), registry: true, inn, phone: '+70000000001' });
      await use({ admin, code, inn, quarter, previous });
    } finally {
      cleanupClient(admin, code);
    }
  },
});

describe('client registry', { session: 'admin' }, () => {
  test('finds a client by part of the KCR code and opens its card', async ({ app, screen, client }) => {
    await app.open('/');
    await screen.getByLabel('Код КЦР или ИНН').fill(client.code.slice(4));
    await screen.getByRole('button', 'Применить').tap();
    await screen.getByRole('button', client.code).tap();

    await expect(screen.getByRole('heading', { name: new RegExp(client.code) })).toBeVisible();
    await expect(screen.getByText(client.inn, { exact: true })).toBeVisible();
    await screen.getByRole('tab', 'История наблюдений').tap();
    await expect(screen.getByRole('heading', 'История наблюдений')).toBeVisible();
    await screen.getByRole('tab', 'Лента событий').tap();
    await expect(screen.getByRole('heading', 'Лента событий')).toBeVisible();
  });

  test('searches by INN as an operator', { session: 'operator_a' }, async ({ app, screen, client }) => {
    await app.open('/');
    await screen.getByLabel('Код КЦР или ИНН').fill(client.inn.slice(1));
    await screen.getByRole('button', 'Применить').tap();
    await expect(screen.getByText('Клиенты не найдены', { exact: true })).toBeVisible();
    await app.open(`/client/${client.code}`);
    await expect(screen.getByText('Клиент недоступен', { exact: true })).toBeVisible();

    await publish(client.admin, client.code, client.quarter);
    await app.open('/');
    await screen.getByLabel('Код КЦР или ИНН').fill(client.inn.slice(1));
    await screen.getByRole('button', 'Применить').tap();
    await expect(screen.getByRole('button', client.code)).toBeVisible();
  });

  test('shows an empty result and resets search filters', async ({ app, screen, client }) => {
    await app.open('/');
    await screen.getByLabel('Код КЦР или ИНН').fill(`${client.code}-missing`);
    await screen.getByRole('button', 'Применить').tap();
    await expect(screen.getByText('Клиенты не найдены', { exact: true })).toBeVisible();

    await screen.getByRole('button', 'Сбросить').tap();

    await expect(screen.getByLabel('Код КЦР или ИНН')).toHaveValue('');
    await expect(screen.getByText('Клиенты не найдены', { exact: true })).toHaveCount(0);
  });

  test('paginates matching clients in stable KCR order', async () => {
    const admin = await signIn(accounts.admin);
    const prefix = newCode().slice(0, -1);
    const codes = ['3', '1', '2'].map(suffix => prefix + suffix);
    const quarter = quarterAt(businessDate());
    try {
      for (const code of codes) applyClient(admin, { code, quarter, registry: true });

      const page = async (number: number) => {
        const reply = await api(`/clients?search=${prefix}&page_size=2&page=${number}`, { actor: admin });
        expect(reply.status).toBe(200);
        expect(reply.body.total).toBe(3);
        return reply.body.items.map((item: { kcr_code: string }) => item.kcr_code);
      };

      expect(await page(1)).toEqual([prefix + '1', prefix + '2']);
      expect(await page(2)).toEqual([prefix + '3']);
    } finally {
      for (const code of codes) cleanupClient(admin, code);
    }
  });

  for (const [name, current, label] of [
    ['decreasing', 4, 'Уменьшается'],
    ['increasing', 24, 'Растёт'],
    ['unchanged', 10, 'Без изменений'],
    ['all submitted', 0, 'Все переданы'],
  ] as const) {
    test(`shows ${name} dynamics against the preceding quarter`, async ({ app, browser, client }) => {
      const { admin, code, quarter, previous } = client;
      applyClient(admin, { code, quarter: previous, remaining: 10, date: dateInQuarter(previous, 2) });
      applyClient(admin, { code, quarter, remaining: current, date: dateInQuarter(quarter) });
      await app.open(`/client/${code}`);

      const comparison = browser.locator('.comparison-line');
      const name = (value: string) => `${['I', 'II', 'III', 'IV'][Number(value.at(-1)) - 1]} квартал ${value.slice(0, 4)}`;
      await expect(comparison).toContainText(`${name(previous)} 10`);
      await expect(comparison).toContainText(`${name(quarter)} ${current}`);
      await expect(comparison).toContainText(label);
    });
  }

  test('leaves dynamics undefined without comparable quarters', async ({ app, screen, client }) => {
    const { admin, code, quarter } = client;
    applyClient(admin, { code, quarter, remaining: 5, date: dateInQuarter(quarter) });
    await app.open(`/client/${code}`);

    await expect(screen.getByText('Не определена', { exact: true })).toBeVisible();
  });

  test('filters counts and percentages using the selected quarter balance', async ({ app, screen, client }) => {
    const { admin, code, quarter, previous } = client;
    applyClient(admin, { code, quarter: previous, remaining: 40, date: dateInQuarter(previous, 2) });
    applyClient(admin, { code, quarter, remaining: 5, date: dateInQuarter(quarter) });
    await app.open('/');
    await screen.getByLabel('Код КЦР или ИНН').fill(code);
    await screen.getByLabel('Квартал', { exact: true }).fill(quarter);
    await screen.getByLabel('Не передано до').fill('5');
    await screen.getByLabel('Процент до').fill('5');
    await screen.getByLabel('Признак').selectOption('VIP');
    await screen.getByRole('button', 'Применить').tap();
    await expect(screen.getByRole('row').filter({ hasText: code })).toContainText('45');

    await screen.getByLabel('Процент до').fill('4');
    await screen.getByRole('button', 'Применить').tap();

    await expect(screen.getByText('Клиенты не найдены', { exact: true })).toBeVisible();
  });

  test('distinguishes continuous two-quarter delay from a new delay after zero', async ({ client }) => {
    const { admin, code, quarter, previous } = client;
    const category = async (duration: string) => {
      const reply = await api(`/clients?search=${code}&duration=${duration}`, { actor: admin });
      expect(reply.status).toBe(200);
      return reply.body.total;
    };
    applyClient(admin, { code, quarter: previous, remaining: 10, date: dateInQuarter(previous, 2) });
    expect(await category('FIRST_TIME')).toBe(1);
    applyClient(admin, { code, quarter, remaining: 6, date: dateInQuarter(quarter) });
    expect(await category('LONG_DELAY_2Q')).toBe(1);

    applyClient(admin, { code, quarter: previous, remaining: 0, date: dateInQuarter(quarter, 2) });
    applyClient(admin, { code, quarter, remaining: 0, date: dateInQuarter(quarter, 2) });
    applyClient(admin, { code, quarter, remaining: 3, date: dateInQuarter(quarter, 3) });

    expect(await category('FIRST_TIME')).toBe(1);
    expect(await category('LONG_DELAY_2Q')).toBe(0);
  });
});
