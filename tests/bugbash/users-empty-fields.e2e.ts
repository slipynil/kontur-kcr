import { describe, test } from '@e2e-dev/web';
import { expect } from 'e2e';

// The import forms validate in the handler and render a visible role=alert message
// (management.tsx:37-38 documents the convention: native required bubbles are
// invisible to the a11y tree), but the add-user form still relies on native
// required alone: an empty submit is blocked silently with no visible message.
// Screen-only steps: the /users table overflows the QA model's context window.
describe('users', { tags: ['bugbash'], session: 'admin' }, () => {
  test('creating a user with empty fields shows a visible validation message', async ({ app, screen }) => {
    await app.open('/users');

    await screen.getByRole('button', 'Добавить пользователя').tap();
    await screen.getByRole('button', 'Создать пользователя').tap();

    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toBeVisible();
  });
});
