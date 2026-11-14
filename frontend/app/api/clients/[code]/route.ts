import { cookies } from "next/headers";
import { clients, sessions } from "@/lib/backend";

export async function GET(_request: Request, { params }: { params: Promise<{ code: string }> }) {
  const token = (await cookies()).get("kontur_session")?.value;
  if (!token || !sessions.has(token)) return Response.json({ error: "Не авторизован" }, { status: 401 });
  const { code } = await params;
  const client = clients.get(code);
  return client ? Response.json({ code, owner: client.owner }) : Response.json({ error: "Клиент не найден" }, { status: 404 });
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ code: string }> }) {
  const token = (await cookies()).get("kontur_session")?.value;
  const session = token ? sessions.get(token) : undefined;
  if (!session) return Response.json({ error: "Не авторизован" }, { status: 401 });
  if (session.role !== "Администратор") return Response.json({ error: "Недостаточно прав" }, { status: 403 });
  const { code } = await params;
  const client = clients.get(code);
  if (!client) return Response.json({ error: "Клиент не найден" }, { status: 404 });
  client.owner = null;
  return Response.json({ code, owner: null, released: true });
}
