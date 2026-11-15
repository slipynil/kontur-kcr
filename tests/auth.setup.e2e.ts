import { test } from '@e2e-dev/web';
import { credentials, expect } from 'e2e';

test.setup('sign in as admin', { sessions: ['admin'] }, async ({ app, agent, screen, session }) => {
  const admin = credentials.user('admin');

  await app.open('/');
  await agent.act('sign in with the given credentials', {
    params: {
      username: admin.username,
      password: admin.password, // a Secret: the model sees its name, the runner fills the field
    },
  });

  // The admin-only navigation link proves the session took before it is saved
  // and confirms the goal for the replay cache.
  await expect(screen.getByRole('button', 'Загрузка реестра')).toBeVisible();
  await session.save('admin');
});
