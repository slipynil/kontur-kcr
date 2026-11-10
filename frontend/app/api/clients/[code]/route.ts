import { cookies } from "next/headers";
import { clients, validSession } from "@/lib/backend";
import { actorId, operatorName, operatorWork, operatorWorkEnabled } from "@/lib/operator-work";

export async function GET(_request: Request, { params }: { params: Promise<{ code: string }> }) {
  const token = (await cookies()).get("kontur_session")?.value;
  const session = validSession(token);
  if (!session) return Response.json({ error: "Не авторизован" }, { status: 401 });
  const { code } = await params;
  const client = clients.get(code);
  if (client && operatorWorkEnabled()) {
    const result = await operatorWork(session, "/clients/" + encodeURIComponent(code) + "/assignment",
      { method: "GET", cycle: true });
    if (result.status === 404) {
      return Response.json({ code, owner: null, operatorWork: true, published: false });
    }
    if (!result.ok) return result;
    const assignment = await result.json();
    return Response.json({
      code,
      owner: operatorName(assignment.assigned_operator_id),
      operatorWork: true,
      published: true,
      assignmentId: assignment.assignment_id,
      workQuarter: assignment.work_quarter,
      stage: assignment.stage,
      waitUntil: assignment.wait_until,
      awaitingBalance: assignment.awaiting_balance,
      ownerIsMe: assignment.assigned_operator_id === actorId(session.username),
    });
  }
  return client ? Response.json({ code, owner: client.owner }) : Response.json({ error: "Клиент не найден" }, { status: 404 });
}

export async function DELETE(_request: Request, { params }: { params: Promise<{ code: string }> }) {
  const token = (await cookies()).get("kontur_session")?.value;
  const session = validSession(token);
  if (!session) return Response.json({ error: "Не авторизован" }, { status: 401 });
  if (session.role !== "Администратор") return Response.json({ error: "Недостаточно прав" }, { status: 403 });
  const { code } = await params;
  const client = clients.get(code);
  if (!client) return Response.json({ error: "Клиент не найден" }, { status: 404 });
  if (operatorWorkEnabled()) return Response.json({ error: "Снятие с оператора недоступно" }, { status: 405 });
  client.owner = null;
  return Response.json({ code, owner: null, released: true });
}
