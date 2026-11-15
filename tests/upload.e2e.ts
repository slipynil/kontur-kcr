import { existsSync } from 'node:fs';
import type { Browser } from '@e2e-dev/web';
import { describe, test } from '@e2e-dev/web';
import { expect } from 'e2e';

// Real КЦР workbooks live in data/ on the dev server only: выгрузки не публикуются
// в открытом репозитории (AGENTS.md), so the directory is git-ignored.
const REGISTRY_FILE = 'data/КЦР_последняя_выгрузка_Дубна.xlsx';
const SNAPSHOT_FILE = 'data/Срез_20.10.26_Дубна.xlsx';

const skipWithoutFixtures = (...files: string[]) => {
  const missing = files.filter((file) => !existsSync(file));
  if (missing.length) test.skip(`workbook fixtures are not checked out: ${missing.join(', ')}`);
};

// The report renders each counter as <div class="stat-card"><small>{label}</small><b>{value}</b></div>.
const statCard = (browser: Browser, label: string) =>
  browser.locator(`xpath=//small[normalize-space()="${label}"]/following-sibling::b`);

describe('upload', { session: 'admin' }, () => {
  test('imports the registry workbook and applies every valid row', { timeout: 300_000 }, async ({ app, screen, browser }) => {
    skipWithoutFixtures(REGISTRY_FILE);

    await app.open('/upload');

    await screen.getByLabel('Файл реестра .xlsx').setInputFiles(REGISTRY_FILE);
    await screen.getByLabel('Квартал').fill('2026_Q4');
    await screen.getByLabel('Дата среза').fill('2026-11-15');
    await screen.getByRole('button', 'Загрузить').tap();

    await expect(browser).toHaveURL(/\/import-report\?batch=/, { timeout: 30_000 });
    await expect(screen.getByRole('heading', { name: 'КЦР_последняя_выгрузка_Дубна.xlsx', level: 2 })).toBeVisible();
    // The worker parses and applies the file asynchronously while the report polls.
    await expect(screen.getByText(/2026_Q4 · 2026-11-15 ·/)).toBeVisible({ timeout: 60_000 });
    // The counters are the outcome: every parseable row reaches Clients Service
    // (5873 accepted; one row has a broken Excel formula and is reported as an error).
    await expect(statCard(browser, 'Строк в файле')).toHaveText('5874', { timeout: 180_000 });
    await expect(statCard(browser, 'Принято')).toHaveText('5873', { timeout: 180_000 });
    await expect(statCard(browser, 'Ошибок')).toHaveText('1', { timeout: 180_000 });
  });

  test('imports the snapshot workbook for the selected quarter', { timeout: 300_000 }, async ({ app, screen, browser }) => {
    skipWithoutFixtures(SNAPSHOT_FILE);

    await app.open('/upload');

    await screen.getByLabel('Файл реестра .xlsx').setInputFiles(SNAPSHOT_FILE);
    await screen.getByLabel('Квартал').fill('2026_Q3');
    await screen.getByLabel('Дата среза').fill('2026-10-20');
    await screen.getByLabel('Тип импорта').selectOption({ label: 'Срез' });
    await screen.getByRole('button', 'Загрузить').tap();

    await expect(browser).toHaveURL(/\/import-report\?batch=/, { timeout: 30_000 });
    await expect(screen.getByRole('heading', { name: 'Срез_20.10.26_Дубна.xlsx', level: 2 })).toBeVisible();
    // Only the «3 кв 2026» sheet belongs to 2026_Q3; the yearly sheet and the
    // other-quarter sheets stay in the file and are reported as row errors.
    await expect(screen.getByText(/2026_Q3 · 2026-10-20 ·/)).toBeVisible({ timeout: 60_000 });
    // 243 snapshot rows reference КЦР codes absent from the registry and are
    // rejected by Clients Service («Код КЦР отсутствует в реестре»).
    await expect(statCard(browser, 'Строк в файле')).toHaveText('6824', { timeout: 180_000 });
    await expect(statCard(browser, 'Принято')).toHaveText('3160', { timeout: 180_000 });
    await expect(statCard(browser, 'Ошибок')).toHaveText('3666', { timeout: 180_000 });
  });

  test('rejects a file that is not .xlsx without uploading it', async ({ app, screen, browser }) => {
    await app.open('/upload');

    await screen.getByLabel('Файл реестра .xlsx').setInputFiles('package.json');
    await screen.getByLabel('Квартал').fill('2026_Q4');
    await screen.getByLabel('Дата среза').fill('2026-11-15');
    await screen.getByRole('button', 'Загрузить').tap();

    // Next.js renders an empty aria-live route announcer with role="alert"; filter it out.
    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toContainText('Выберите файл .xlsx');
    await expect(browser).toHaveURL('/upload');
  });
});
