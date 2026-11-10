"use client";

import { useEffect, useState } from "react";

type Notification = {
  id: string;
  code: string;
  expiresAt: string;
  status: "PENDING" | "REJECTED" | "TRANSFERRED" | "CANCELLED";
  incoming: boolean;
  requester: string;
};

const statuses = {
  PENDING: "Запрос отправлен",
  REJECTED: "Ответственный отказал",
  TRANSFERRED: "Клиент перезакреплён",
  CANCELLED: "Запрос отменён",
};

async function loadNotifications(signal?: AbortSignal): Promise<Notification[]> {
  const response = await fetch("/api/operator-work", { signal });
  if (!response.ok) throw new Error("Не удалось загрузить уведомления");
  return (await response.json()).items ?? [];
}

export default function ReassignmentNotifications() {
  const [items, setItems] = useState<Notification[]>([]);
  const [rejecting, setRejecting] = useState<string | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    const poll = async () => {
      try {
        const notifications = await loadNotifications(controller.signal);
        if (!controller.signal.aborted) setItems(notifications);
      } catch {
        // The next poll retries transient failures.
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    };
    void poll();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, []);

  async function reject(id: string) {
    setRejecting(id);
    setMessage("");
    try {
      const response = await fetch("/api/operator-work", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id }),
      });
      setMessage(response.ok ? "Запрос отклонён. Клиент остаётся за вами." :
        ((await response.json()).message ?? "Не удалось отклонить запрос"));
      setItems(await loadNotifications());
    } catch {
      setMessage("Не удалось обновить запрос. Проверьте его статус.");
    } finally {
      setRejecting(null);
    }
  }

  const visible = items.filter(item => !item.incoming || item.status === "PENDING");
  if (!visible.length && !message) return null;

  return <section className="panel rules" aria-label="Уведомления о перезакреплении">
    <h3>Перезакрепление клиентов</h3>
    {message && <p role="status">{message}</p>}
    {visible.map(item => <div className="status-row" key={item.id}>
      <a className="blue-text" href={`/client/${encodeURIComponent(item.code)}`}>{item.code}</a>
      {item.incoming ? <>
        <span> {item.requester} запросил перезакрепление. Ответьте до {new Date(item.expiresAt)
          .toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short", timeZone: "Europe/Moscow" })} мск.</span>
        <button type="button" className="button secondary" disabled={rejecting !== null}
          onClick={() => void reject(item.id)}>Отказать</button>
      </> : <span> {statuses[item.status]}</span>}
    </div>)}
  </section>;
}
