/** @jest-environment node */
import { cookies } from 'next/headers'
import { clients, sessions, users } from '../lib/backend'
import { POST as login, GET as authStatus, DELETE as logout } from '../app/api/auth/route'
import { GET as listUsers, POST as createUser } from '../app/api/users/route'
import { GET as getClient, DELETE as releaseClient } from '../app/api/clients/[code]/route'
import { POST as claimClient } from '../app/api/clients/[code]/claim/route'
jest.mock('next/headers', () => ({ cookies: jest.fn() }))
const jar = { get: jest.fn(), set: jest.fn(), delete: jest.fn() }
const ctx = (code = 'AAA00412') => ({ params: Promise.resolve({ code }) })
const req = (body: unknown) => new Request('http://localhost', { method: 'POST', body: JSON.stringify(body) })
beforeEach(() => { jest.clearAllMocks(); (cookies as jest.Mock).mockResolvedValue(jar); jar.get.mockReturnValue(undefined) })
afterEach(() => { sessions.clear(); clients.forEach(c => c.owner = null); users.delete('test.user') })
it('covers login, session lookup, inactive sessions, logout and errors', async () => {
 expect((await login(new Request('http://x',{method:'POST',body:'{'}))).status).toBe(401)
 expect((await login(req({username:'operator',password:'bad'}))).status).toBe(401)
 const response=await login(req({username:'operator',password:'1234'})); expect(response.status).toBe(200)
 const token=jar.set.mock.calls[0][1]; jar.get.mockReturnValue({value:token}); expect((await authStatus()).status).toBe(200)
 users.get('operator')!.active=false; expect((await authStatus()).status).toBe(401); users.get('operator')!.active=true
 sessions.set(token,{username:'operator',name:'Анна К.',role:'Оператор'}); expect((await logout()).status).toBe(200)
})
it('covers admin user listing and creation validation', async () => {
 expect((await listUsers()).status).toBe(403)
 sessions.set('a',{username:'vera.v',name:'Вера В.',role:'Администратор'}); jar.get.mockReturnValue({value:'a'})
 expect((await listUsers()).status).toBe(200)
 for(const body of [{username:'x',name:'ok',password:'1234',role:'Оператор'},{username:'test.user',name:'x',password:'1234',role:'Оператор'},{username:'test.user',name:'Name',password:'1',role:'Оператор'},{username:'test.user',name:'Name',password:'1234',role:'bad'}]) expect((await createUser(req(body))).status).toBe(400)
 expect((await createUser(req({username:'test.user',name:'Test User',password:'pass123',role:'Оператор'}))).status).toBe(201)
 expect((await createUser(req({username:'test.user',name:'Test User',password:'pass123',role:'Оператор'}))).status).toBe(409)
})
it('covers client authorization, lookup, release and claim branches', async () => {
 expect((await getClient(new Request('http://x'),ctx())).status).toBe(401)
 sessions.set('o',{username:'operator',name:'Анна К.',role:'Оператор'}); jar.get.mockReturnValue({value:'o'})
 expect((await getClient(new Request('http://x'),ctx())).status).toBe(200); expect((await getClient(new Request('http://x'),ctx('missing'))).status).toBe(404)
 expect((await releaseClient(new Request('http://x'),ctx())).status).toBe(403)
 expect((await claimClient(new Request('http://x'),ctx())).status).toBe(200)
 clients.get('AAA00412')!.owner='Олег П.'; expect((await claimClient(new Request('http://x'),ctx())).status).toBe(409)
 sessions.set('a',{username:'vera.v',name:'Вера В.',role:'Администратор'}); jar.get.mockReturnValue({value:'a'})
 expect((await claimClient(new Request('http://x'),ctx())).status).toBe(403)
 expect((await releaseClient(new Request('http://x'),ctx('missing'))).status).toBe(404); expect((await releaseClient(new Request('http://x'),ctx())).status).toBe(200)
})
