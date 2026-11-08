import { cookies } from "next/headers";
import { addUser, sessions, users } from "@/lib/backend";

async function adminSession() {
  const token = (await cookies()).get("kontur_session")?.value;
  const session = token ? sessions.get(token) : undefined;
  return session?.role === "Администратор" ? session : null;
}

export async function GET() {
  if (!await adminSession()) return Response.json({ error: "Недостаточно прав" }, { status: 403 });
  return Response.json({ users: [...users.values()].map(({ username, name, role, active }) => ({ username, name, role, active, clients: username === "anna.k" ? 6 : username === "oleg.p" ? 4 : 0 })) });
}

export async function POST(request: Request) {
  if (!await adminSession()) return Response.json({ error: "Недостаточно прав" }, { status: 403 });
  const body = await request.json().catch(() => null);
  const username = typeof body?.username === "string" ? body.username.trim().toLowerCase() : "";
  const name = typeof body?.name === "string" ? body.name.trim() : "";
  const password = typeof body?.password === "string" ? body.password : "";
  const role = body?.role;
  if (!/^[a-z0-9._-]{3,32}$/.test(username)) return Response.json({ error: "Логин: 3–32 символа, латиница, цифры, точка, дефис или подчёркивание" }, { status: 400 });
  if (name.length < 2 || name.length > 80) return Response.json({ error: "Укажите имя пользователя" }, { status: 400 });
  if (password.length < 4 || password.length > 128) return Response.json({ error: "Пароль должен содержать от 4 до 128 символов" }, { status: 400 });
  if (role !== "Администратор" && role !== "Оператор") return Response.json({ error: "Выберите роль" }, { status: 400 });
  if (users.has(username)) return Response.json({ error: "Пользователь с таким логином уже существует" }, { status: 409 });
  const user = addUser({ username, name, password, role });
  return Response.json({ user: { username: user.username, name: user.name, role: user.role, active: user.active, clients: 0 } }, { status: 201 });
}
