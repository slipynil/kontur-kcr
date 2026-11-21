import { describe, test } from '@e2e-dev/web';
import { expect } from 'e2e';
import { randomUUID } from 'node:crypto';
import { accounts, api, signIn } from './kontur-fixtures';

describe('role-based access', () => {
  test('shows the administrative sections and signs out of them', { session: 'admin' }, async ({ app, screen }) => {
    await app.open('/users');
    await expect(screen.getByRole('heading', 'Пользователи и роли')).toBeVisible();
    for (const name of ['Клиенты', 'Загрузка реестра', 'Отчёт об импорте', 'Пользователи']) {
      await expect(screen.getByRole('link', name)).toBeVisible();
    }

    await screen.getByRole('button', 'Выйти').tap();
    await app.open('/users');

    await expect(screen.getByRole('button', 'Войти')).toBeVisible();
    await expect(screen.getByRole('heading', 'Пользователи и роли')).toHaveCount(0);
  });

  for (const path of ['/upload', '/import-report', '/users']) {
    test(`denies operator access to ${path} opened directly`, { session: 'operator_a' }, async ({ app, screen }) => {
      await app.open(path);

      await expect(screen.getByRole('link', 'Клиенты')).toBeVisible();
      for (const name of ['Загрузка реестра', 'Отчёт об импорте', 'Пользователи']) {
        await expect(screen.getByRole('link', name)).toHaveCount(0);
      }
      await expect(screen.getByText('Раздел доступен только администратору.', { exact: true })).toBeVisible();
    });
  }

  test('rejects admin-only API requests from an operator', { session: 'operator_a' }, async () => {
    const actor = await signIn(accounts.operator_a);

    const replies = await Promise.all([
      api('/users', { actor }),
      api('/imports', { actor, method: 'POST' }),
      api(`/imports/${randomUUID()}`, { actor }),
    ]);

    expect(replies.map(reply => reply.status)).toEqual([403, 403, 403]);
  });

  for (const [role, label] of [['OPERATOR', 'Оператор'], ['ADMIN', 'Администратор']] as const) {
    test(`creates a ${role} account that can sign in`, { session: 'admin' }, async ({ app, screen }) => {
      const login = `kontur-e2e-${randomUUID().slice(0, 10)}`;
      const password = 'kontur-e2e-new-user-123';
      await app.open('/users');

      await screen.getByRole('button', 'Добавить пользователя').tap();
      await screen.getByLabel('Логин').fill(login);
      await screen.getByLabel('Пароль').fill(password);
      await screen.getByLabel('Роль').selectOption({ label });
      await screen.getByRole('button', 'Создать пользователя').tap();

      await expect(screen.getByText('Пользователь создан', { exact: true })).toBeVisible();
      expect((await signIn({ login, password, role })).user.role).toBe(role);
    });
  }

  test('shows an error when creating a duplicate login', { session: 'admin' }, async ({ app, screen }) => {
    await app.open('/users');
    await screen.getByRole('button', 'Добавить пользователя').tap();
    await screen.getByLabel('Логин').fill(accounts.admin.login);
    await screen.getByLabel('Пароль').fill('kontur-e2e-new-user-123');

    await screen.getByRole('button', 'Создать пользователя').tap();

    await expect(screen.getByRole('alert').filter({ hasText: /занят/i })).toBeVisible();
  });
});
