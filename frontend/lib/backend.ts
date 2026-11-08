import { randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

type Session = { username: string; name: string; role: "Администратор" | "Оператор" };
type ClientState = { owner: string | null };
export type AppUser = { username: string; name: string; role: "Администратор" | "Оператор"; salt: string; passwordHash: string; active: boolean };

const store = globalThis as typeof globalThis & {
  konturSessions?: Map<string, Session>;
  konturClients?: Map<string, ClientState>;
  konturUsers?: Map<string, AppUser>;
};

export const sessions = store.konturSessions ??= new Map();
export const users = store.konturUsers ??= new Map();
function seedUser(username:string,name:string,role:AppUser["role"],password:string,active=true){const salt=randomBytes(16).toString("hex");return {username,name,role,salt,passwordHash:scryptSync(password,salt,64).toString("hex"),active}}
if(users.size===0){for(const user of [seedUser("vera.v","Вера В.","Администратор","1234"),seedUser("operator","Анна К.","Оператор","1234"),seedUser("anna.k","Анна К.","Оператор","1234"),seedUser("oleg.p","Олег П.","Оператор","1234"),seedUser("ivan.s","Иван С.","Оператор","1234",false)])users.set(user.username,user)}
export function addUser(input:{username:string;name:string;role:AppUser["role"];password:string}){const salt=randomBytes(16).toString("hex");const user={username:input.username,name:input.name,role:input.role,salt,passwordHash:scryptSync(input.password,salt,64).toString("hex"),active:true};users.set(user.username,user);return user}
export function verifyPassword(user:AppUser,password:string){const hash=scryptSync(password,user.salt,64);return timingSafeEqual(hash,Buffer.from(user.passwordHash,"hex"))}
export const clients = store.konturClients ??= new Map([
  ["AAA00412", { owner: null }], ["AAA02210", { owner: "Анна К." }],
  ["AAA01877", { owner: null }], ["AAA03345", { owner: "Анна К." }],
  ["AAA00981", { owner: "Анна К." }], ["AAA00560", { owner: "Анна К." }],
  ["AAA00073", { owner: null }], ["AAA04012", { owner: null }],
]);

export function newSession(session: Session) {
  const token = randomBytes(32).toString("hex");
  sessions.set(token, session);
  return token;
}
export function validSession(token: string | undefined) {
  const session = token ? sessions.get(token) : undefined;
  const account = session ? users.get(session.username) : undefined;
  if (!session || !account?.active) return undefined;
  return { username: account.username, name: account.name, role: account.role } as Session;
}
