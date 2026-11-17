import { test } from '@e2e-dev/web';
import { credentials, expect } from 'e2e';
import { prepareOperators } from './kontur-fixtures';

test.setup('sign in as admin', { sessions: ['admin'] }, async ({ app, screen, session }) => {
  const admin = credentials.user('admin');

  await app.open('/');
  await screen.getByPlaceholder('Логин').fill(admin.username);
  await screen.getByPlaceholder('Пароль').fill(admin.password);
  await screen.getByRole('button', 'Войти').tap();
  await expect(screen.getByRole('button', 'Загрузка реестра')).toBeVisible();
  await session.save('admin');
});

test.setup('sign in as both operators', { sessions: ['operator_a', 'operator_b'] }, async ({ app, screen, session }) => {
  await prepareOperators();
  for (const name of ['operator_a', 'operator_b']) {
    const operator = credentials.user(name);
    await app.open('/');
    await screen.getByPlaceholder('Логин').fill(operator.username);
    await screen.getByPlaceholder('Пароль').fill(operator.password);
    await screen.getByRole('button', 'Войти').tap();
    await expect(screen.getByText(`${operator.username} · Оператор`, { exact: true })).toBeVisible();
    await session.save(name);
    await screen.getByRole('button', 'Выйти').tap();
    await expect(screen.getByRole('button', 'Войти')).toBeVisible();
  }
});
