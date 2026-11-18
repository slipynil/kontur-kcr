import { describe, test } from '@e2e-dev/web';
import { expect } from 'e2e';

describe('login', () => {
  test('submitting the login form reaches the API instead of a 404 page', async ({ app, screen }) => {
    await app.open('/');

    await screen.getByLabel('Логин', { exact: true }).fill('e2e-probe');
    await screen.getByLabel('Пароль', { exact: true }).fill('definitely-wrong-password');
    await screen.getByRole('button', 'Войти').tap();

    // The message comes from the Gateway JSON response. Before the proxy fix
    // the XHR hit a Next.js prerendered 404 page and the form showed
    // «Ошибка запроса (404)» instead. Next.js also renders an empty
    // aria-live route announcer with role="alert", so filter it out.
    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toContainText('Неверный логин или пароль');
  });
});
