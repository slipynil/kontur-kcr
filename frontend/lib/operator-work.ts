import { createHash } from "node:crypto";
import { users } from "@/lib/backend";

type Actor = { username: string; role: "Администратор" | "Оператор" };

export function operatorWorkEnabled() {
  return Boolean(process.env.OPERATOR_WORK_URL);
}

// Demo accounts are local. Gateway uses its own stable account UUIDs in deployment.
export function actorId(username: string) {
  const id = createHash("sha256").update("kontur-operator-work-demo:" + username).digest("hex");
  return id.replace(/^(.{8})(.{4}).(.{3}).(.{3})(.{12}).*$/, "$1-$2-8$3-8$4-$5");
}

export function operatorName(id: string | null) {
  if (!id) return null;
  if (id === "00000000-0000-0000-0000-000000000000") return "Система";
  return [...users.values()].find(user => actorId(user.username) === id)?.name ?? id;
}

export async function operatorWork(
  actor: Actor,
  path: string,
  options?: { method: string; body?: unknown; cycle?: boolean },
) {
  const base = process.env.OPERATOR_WORK_URL;
  const token = process.env.OPERATOR_WORK_SERVICE_TOKEN;
  const quarter = process.env.OPERATOR_WORK_QUARTER;
  if (!base || !token || (options?.cycle && !quarter)) {
    return Response.json({ message: "Operator Work не настроен" }, { status: 503 });
  }
  try {
    const url = new URL("/internal/v1" + path, base);
    if (options?.cycle && quarter) url.searchParams.set("work_quarter", quarter);
    return await fetch(url, {
      method: options?.method ?? "GET",
      headers: {
        Authorization: "Bearer " + token,
        "X-User-Id": actorId(actor.username),
        "X-User-Role": actor.role === "Оператор" ? "OPERATOR" : "ADMIN",
        "Content-Type": "application/json",
      },
      body: options?.body === undefined ? undefined : JSON.stringify(options.body),
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
  } catch {
    return Response.json({ message: "Operator Work временно недоступен" }, { status: 503 });
  }
}
