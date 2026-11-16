import { describe, test } from '@e2e-dev/web';
import { expect } from 'e2e';

// Same family as the add-user form: the login form relies on native required
// alone, so an empty submit is blocked silently with no visible message
// (management.tsx:37-38 documents the project convention against this).
// Screen-only steps keep the repro deterministic and model-free.
describe('login', { tags: ['bugbash'] }, () => {
  test('submitting the login form empty shows a visible validation message', async ({ app, screen }) => {
    await app.open('/');

    await screen.getByRole('button', 'Войти').tap();

    const formError = screen.getByRole('alert').filter({ hasText: /\S/ });
    await expect(formError).toBeVisible();
  });
});
