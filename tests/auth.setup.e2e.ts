import { test } from '@e2e-dev/web';
import { credentials, expect } from 'e2e';

test.setup('sign in as admin', { sessions: ['admin'] }, async ({ app, screen, session }) => {
  const admin = credentials.user('admin');

  await app.open('/');
  await screen.getByLabel('Логин').fill(admin.username);
  await screen.getByLabel('Пароль').fill(admin.password);
  await screen.getByRole('button', 'Войти').tap();

  // The admin-only navigation link proves the session took before it is saved.
  await expect(screen.getByRole('button', 'Загрузка реестра')).toBeVisible();
  await session.save('admin');
});
