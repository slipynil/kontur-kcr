import { cookies } from "next/headers";
import { clients, validSession } from "@/lib/backend";
import { operatorWork, operatorWorkEnabled } from "@/lib/operator-work";

export async function POST(request: Request, { params }: { params: Promise<{ code: string }> }) {
  const jar = await cookies();
  const token = jar.get("kontur_session")?.value;
  const session = validSession(token);
  if (!session) return Response.json({ error: "Войдите в систему" }, { status: 401 });
  if (session.role !== "Оператор") return Response.json({ error: "Взять клиента в работу может только оператор" }, { status: 403 });
  const { code } = await params;
  const client = clients.get(code);
  if (!client) return Response.json({ error: "Клиент не найден" }, { status: 404 });
  if (operatorWorkEnabled()) {
    const body = await request.json().catch(() => null);
    return operatorWork(session, "/clients/" + encodeURIComponent(code) + "/take", {
      method: "POST", body, cycle: true,
    });
  }
  if (client.owner && client.owner !== session.name) {
    return Response.json({ error: "Клиента уже взял в работу другой пользователь", owner: client.owner }, { status: 409 });
  }
  client.owner = session.name;
  return Response.json({ code, owner: client.owner, claimed: true });
}
