import { cookies } from "next/headers";
import { clients, validSession } from "@/lib/backend";
import { operatorName, operatorWork } from "@/lib/operator-work";

type Context = { params: Promise<{ code: string }> };

export async function GET(request: Request, { params }: Context) {
  const user = validSession((await cookies()).get("kontur_session")?.value);
  if (!user) return Response.json({ message: "Войдите в систему" }, { status: 401 });
  const { code } = await params;
  if (!clients.has(code)) return Response.json({ message: "Клиент не найден" }, { status: 404 });
  const query = new URLSearchParams({ page: new URL(request.url).searchParams.get("page") ?? "1" });
  const response = await operatorWork(user, "/clients/" + encodeURIComponent(code) + "/events?" + query);
  if (!response.ok) return response;
  const history = await response.json();
  return Response.json({
    ...history,
    items: history.items.map((event: { author_id: string }) => ({
      ...event,
      author_name: operatorName(event.author_id),
    })),
  });
}

export async function POST(request: Request, { params }: Context) {
  const user = validSession((await cookies()).get("kontur_session")?.value);
  if (!user) return Response.json({ message: "Войдите в систему" }, { status: 401 });
  if (user.role !== "Оператор") {
    return Response.json({ message: "Событие может сохранить только оператор" }, { status: 403 });
  }
  const { code } = await params;
  if (!clients.has(code)) return Response.json({ message: "Клиент не найден" }, { status: 404 });
  const body = await request.json().catch(() => null);
  return operatorWork(user, "/clients/" + encodeURIComponent(code) + "/events", {
    method: "POST", body,
  });
}
