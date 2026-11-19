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
    await expect(screen.getByLabel('Квартал')).not.toBeVisible();
    await expect(screen.getByLabel('Дата среза')).not.toBeVisible();
    await screen.getByRole('button', 'Загрузить').tap();

    await expect(browser).toHaveURL(/\/import-report\?batch=/, { timeout: 30_000 });
    await expect(screen.getByRole('heading', { name: 'КЦР_последняя_выгрузка_Дубна.xlsx', level: 2 })).toBeVisible();
    // The worker parses and applies the file asynchronously while the report polls.
    await expect(screen.getByText('Завершён', { exact: true })).toBeVisible({ timeout: 60_000 });
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
    await screen.getByLabel('Тип импорта').selectOption({ label: 'Срез' });
    await screen.getByLabel('Квартал').fill('2026_Q3');
    await screen.getByLabel('Дата среза').fill('2026-10-20');
    await screen.getByRole('button', 'Загрузить').tap();

    await expect(browser).toHaveURL(/\/import-report\?batch=/, { timeout: 30_000 });
    await expect(screen.getByRole('heading', { name: 'Срез_20.10.26_Дубна.xlsx', level: 2 })).toBeVisible();
    await expect(screen.getByText('Завершён', { exact: true })).toBeVisible({ timeout: 180_000 });
    // Only the «3 кв 2026» sheet contributes rows. The error counter includes
    // 244 rejected rows and one notice for each of the three skipped sheets.
    await expect(statCard(browser, 'Строк в файле')).toHaveText('3404');
    await expect(statCard(browser, 'Принято')).toHaveText('3160');
    await expect(statCard(browser, 'Ошибок')).toHaveText('247');
  });

  test('rejects a file that is not .xlsx without uploading it', async ({ app, screen, browser }) => {
    await app.open('/upload');

    await screen.getByLabel('Файл реестра .xlsx').setInputFiles('package.json');
    await screen.getByRole('button', 'Загрузить').tap();

    // Next.js renders an empty aria-live route announcer with role="alert"; filter it out.
    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toContainText('Выберите файл .xlsx');
    await expect(browser).toHaveURL('/upload');
  });

  // The explore run found «Загрузить» gave no feedback at all when another field
  // was also invalid: native validation silently blocked submit (invisible in
  // headless). Every invalid field must surface a visible message instead.
  test('keeps showing the upload error when the quarter format is wrong too', async ({ app, screen, browser }) => {
    await app.open('/upload');

    await screen.getByLabel('Файл реестра .xlsx').setInputFiles('package.json');
    await screen.getByLabel('Тип импорта').selectOption({ label: 'Срез' });
    await screen.getByLabel('Квартал').fill('2026-Q4');
    await screen.getByLabel('Дата среза').fill('2026-11-15');
    await screen.getByRole('button', 'Загрузить').tap();

    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toContainText('Выберите файл .xlsx');
    await expect(browser).toHaveURL('/upload');
  });
});

describe('import-report', { session: 'admin' }, () => {
  test('explains that the package ID is required', async ({ app, screen, browser }) => {
    await app.open('/import-report');

    await screen.getByRole('button', 'Открыть отчёт').tap();

    // The same empty-announcer filter as in the upload suite.
    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toContainText('Введите ID пакета');
    await expect(browser).toHaveURL('/import-report');
  });

  test('explains the UUID format for a malformed package ID', async ({ app, screen, browser }) => {
    await app.open('/import-report');

    await screen.getByLabel('ID пакета').fill('1');
    await screen.getByRole('button', 'Открыть отчёт').tap();

    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toContainText('ID пакета должен быть UUID');
    await expect(browser).toHaveURL('/import-report');
  });

  test('shows the server not-found error for an unknown UUID package', async ({ app, screen, browser }) => {
    await app.open('/import-report');

    await screen.getByLabel('ID пакета').fill('3f2504e0-4f89-41d4-9a0c-0305e82c3301');
    await screen.getByRole('button', 'Открыть отчёт').tap();

    await expect(browser).toHaveURL('/import-report?batch=3f2504e0-4f89-41d4-9a0c-0305e82c3301');
    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toContainText('Пакет не найден');
  });
});
