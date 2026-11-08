import { addUser, clients, newSession, sessions, users, validSession, verifyPassword } from '../lib/backend'

describe('backend identity and state', () => {
  afterEach(() => {
    sessions.clear()
    clients.forEach(client => { client.owner = null })
  })

  it('verifies correct passwords and rejects incorrect ones', () => {
    const user = users.get('operator')!
    expect(verifyPassword(user, '1234')).toBe(true)
    expect(verifyPassword(user, 'wrong')).toBe(false)
  })

  it('creates users with salted password hashes', () => {
    const username = `test-${Date.now()}`
    const user = addUser({ username, name: 'Test User', role: 'Оператор', password: 'pass123' })
    expect(user.passwordHash).not.toBe('pass123')
    expect(verifyPassword(user, 'pass123')).toBe(true)
    expect(user.active).toBe(true)
    users.delete(username)
  })

  it('creates opaque sessions and resolves active accounts', () => {
    const token = newSession({ username: 'operator', name: 'Анна К.', role: 'Оператор' })
    expect(token).toMatch(/^[a-f0-9]{64}$/)
    expect(validSession(token)).toMatchObject({ username: 'operator', role: 'Оператор' })
    users.get('operator')!.active = false
    expect(validSession(token)).toBeUndefined()
    users.get('operator')!.active = true
  })

  it('keeps client ownership in shared state', () => {
    clients.get('AAA00412')!.owner = 'Анна К.'
    expect(clients.get('AAA00412')?.owner).toBe('Анна К.')
  })
})
