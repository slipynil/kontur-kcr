import { cookies } from "next/headers";
import { clients, validSession } from "@/lib/backend";
import { actorId, operatorName, operatorWork, operatorWorkEnabled } from "@/lib/operator-work";

async function session() {
  return validSession((await cookies()).get("kontur_session")?.value);
}

export async function GET(request: Request) {
  const user = await session();
  if (!user) return Response.json({ message: "Войдите в систему" }, { status: 401 });
  if (!operatorWorkEnabled() || user.role !== "Оператор") return Response.json({ items: [] });
  if (new URL(request.url).searchParams.has("pool")) {
    const response = await operatorWork(user, "/pool", { method: "GET", cycle: true });
    if (!response.ok) return response;
    const data = await response.json();
    return Response.json({
      enabled: true,
      items: data.items.map((item: { kcr_code: string; assigned_operator_id: string | null }) => ({
        code: item.kcr_code,
        owner: operatorName(item.assigned_operator_id) ?? "Свободен",
      })),
    });
  }
  const response = await operatorWork(user, "/reassignment-notifications");
  if (!response.ok) return response;
  const data = await response.json();
  return Response.json({
    items: data.items.map((item: {
      id: string; owner_id: string; requester_id: string; kcr_code: string;
      expires_at: string; status: string;
    }) => ({
      id: item.id,
      code: item.kcr_code,
      expiresAt: item.expires_at,
      status: item.status,
      incoming: item.owner_id === actorId(user.username),
      requester: operatorName(item.requester_id),
    })),
  });
}

export async function PATCH(request: Request) {
  const user = await session();
  if (user?.role !== "Оператор") return Response.json({ message: "Нет доступа" }, { status: 403 });
  const body = await request.json().catch(() => null);
  if (typeof body?.id !== "string" || !/^[\da-f-]{36}$/i.test(body.id)) {
    return Response.json({ message: "Некорректный ID запроса" }, { status: 400 });
  }
  return operatorWork(user, "/reassignment-notifications/" + body.id + "/reject", { method: "POST" });
}

export async function POST(request: Request) {
  const user = await session();
  if (user?.role !== "Администратор") return Response.json({ message: "Нет доступа" }, { status: 403 });
  const body = await request.json().catch(() => null);
  if (!Array.isArray(body?.codes) || !body.codes.length || body.codes.length > clients.size ||
      body.codes.some((code: unknown) => typeof code !== "string" || !clients.has(code))) {
    return Response.json({ message: "Укажите клиентов для публикации" }, { status: 400 });
  }
  const result = await operatorWork(user, "/pool/publication", {
    method: "PUT", body: { codes: body.codes }, cycle: true,
  });
  return result.ok ? Response.json({ ok: true }) : result;
}
