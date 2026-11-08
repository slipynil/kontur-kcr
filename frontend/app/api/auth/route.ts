import { cookies } from "next/headers";
import { newSession, sessions, users, validSession, verifyPassword } from "@/lib/backend";

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const account = typeof body?.username === "string" ? users.get(body.username) : undefined;
  if (!account || !account.active || typeof body?.password !== "string" || !verifyPassword(account, body.password)) {
    return Response.json({ error: "Неверный логин или пароль" }, { status: 401 });
  }
  const user = { username: account.username, name: account.name, role: account.role };
  const token = newSession(user);
  (await cookies()).set("kontur_session", token, {
    httpOnly: true, sameSite: "lax", secure: process.env.NODE_ENV === "production", path: "/", maxAge: 60 * 60 * 24 * 7,
  });
  return Response.json({ user });
}

export async function GET() {
  const token = (await cookies()).get("kontur_session")?.value;
  const user = validSession(token);
  if (!user && token) sessions.delete(token);
  return user ? Response.json({ user }) : Response.json({ user: null }, { status: 401 });
}

export async function DELETE() {
  const jar = await cookies();
  const token = jar.get("kontur_session")?.value;
  if (token) sessions.delete(token);
  jar.delete("kontur_session");
  return Response.json({ ok: true });
}
