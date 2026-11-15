import { readSession, saveSession, setToken } from '../lib/api';
const user = { id: 'u', login: 'operator', role: 'OPERATOR' as const, is_active: true, created_at: '2026-01-01' };
beforeEach(() => localStorage.clear());
it('restores the actual role and clears both token and session on logout', () => {
  saveSession({ token: 'jwt', token_type: 'Bearer', expires_in: 3600, user });
  expect(readSession()?.user.role).toBe('OPERATOR');
  setToken(null); expect(readSession()).toBeNull(); expect(localStorage.getItem('kontur-api-session')).toBeNull();
});
it('does not accept expired or malformed sessions', () => {
  saveSession({ token: 'jwt', token_type: 'Bearer', expires_in: -1, user }); expect(readSession()).toBeNull();
  localStorage.setItem('kontur-api-session', '{'); expect(readSession()).toBeNull();
});
