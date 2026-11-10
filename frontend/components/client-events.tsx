"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

type OpenAssignment = {
  assignmentId?: string | null;
  workQuarter?: string;
  ownerIsMe?: boolean;
};

const results = {
  AGREEMENT: "Дозвонились, есть договорённость",
  REFUSAL: "Дозвонились, отказ",
  NO_ANSWER: "Недозвон",
  WRONG_NUMBER: "Неверный номер",
};

type CallResult = keyof typeof results;

function DocumentQuarterField() {
  return <label>Исходные кварталы документов
    <input name="document_quarters" placeholder="2026_Q1, 2026_Q2"
      pattern="[0-9]{4}_Q[1-4](, *[0-9]{4}_Q[1-4])*" />
    <small>Укажите кварталы через запятую. Для общего события оставьте поле пустым.</small>
  </label>;
}

function documentQuarters(form: FormData) {
  return String(form.get("document_quarters") ?? "").split(",").map(value => value.trim()).filter(Boolean);
}

export function CallForm({ code, assignment, onSaved }: {
  code: string;
  assignment: OpenAssignment | null;
  onSaved: (message: string) => void;
}) {
  const [opened, setOpened] = useState<{ id: string; quarter: string } | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const attempt = useRef<{ payload: string; id: string } | null>(null);
  if (!opened && assignment?.ownerIsMe && assignment.assignmentId && assignment.workQuarter) {
    setOpened({ id: assignment.assignmentId, quarter: assignment.workQuarter });
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!opened || saving) return;
    const form = new FormData(event.currentTarget);
    const payload = {
      event_type: "CALL_RESULT",
      expected_assignment_id: opened.id,
      work_quarter: opened.quarter,
      call_result: form.get("result"),
      comment_text: form.get("comment"),
      document_quarters: documentQuarters(form),
      promised_date: form.get("promised") || null,
      wait_until: form.get("wait") || null,
    };
    const signature = JSON.stringify(payload);
    if (attempt.current?.payload !== signature) {
      attempt.current = { payload: signature, id: crypto.randomUUID() };
    }
    setError("");
    setSaving(true);
    try {
      const response = await fetch("/api/clients/" + encodeURIComponent(code) + "/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, request_id: attempt.current.id }),
      });
      const result = await response.json();
      if (!response.ok) {
        setError(result.message || "Не удалось сохранить звонок. Проверьте заполненные поля.");
        return;
      }
      onSaved(payload.wait_until && !result.wait_applied
        ? "Звонок сохранён в истории. Срок ожидания остался без изменений."
        : result.wait_applied ? "Звонок и срок ожидания сохранены" : "Звонок сохранён");
    } catch {
      setError("Не удалось подтвердить сохранение. Повторите отправку с теми же данными.");
    } finally {
      setSaving(false);
    }
  }

  if (!opened) return <p>{assignment ? "Возьмите клиента в работу, чтобы записать звонок." :
    "Загружается назначение клиента…"}</p>;

  const stale = assignment?.assignmentId !== opened.id || !assignment?.ownerIsMe;
  return <form className="call-form" onSubmit={save}>
    {stale && <p role="status">Назначение изменилось. Звонок можно сохранить в истории клиента.</p>}
    <fieldset disabled={saving}>
      <legend>Результат звонка</legend>
      {Object.entries(results).map(([key, text], index) =>
        <label className="radio-row" key={key}>
          <input type="radio" name="result" value={key} defaultChecked={index === 0} /> {text}
        </label>)}
      <div className="call-fields">
        <label>Обещанная дата передачи<input type="date" name="promised" /></label>
        <label>Комментарий<textarea name="comment" maxLength={4000} /></label>
        <DocumentQuarterField />
        <label>Ожидание до<input type="date" name="wait" readOnly={stale} /></label>
        <small>Если указать дату ожидания, клиент останется за вами до её наступления.</small>
      </div>
      {error && <p role="alert">{error}</p>}
      <button className="button primary" type="submit">{saving ? "Сохраняем…" : "Сохранить звонок"}</button>
    </fieldset>
  </form>;
}

function HistoricalCommentForm({ code, onSaved }: { code: string; onSaved: () => void }) {
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const attempt = useRef<{ payload: string; id: string } | null>(null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const comment = String(fields.get("comment") ?? "").trim();
    const occurred = String(fields.get("occurred") ?? "");
    if (!comment || !occurred) {
      setError("Укажите дату события и текст комментария.");
      return;
    }

    const payload = {
      event_type: "HISTORICAL_COMMENT",
      comment_text: comment,
      document_quarters: documentQuarters(fields),
      historical_event_date: occurred + ":00+03:00",
    };
    const signature = JSON.stringify(payload);
    if (attempt.current?.payload !== signature) {
      attempt.current = { payload: signature, id: crypto.randomUUID() };
    }
    setSaving(true);
    setError("");
    setSaved(false);
    try {
      const response = await fetch("/api/clients/" + encodeURIComponent(code) + "/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...payload, request_id: attempt.current.id }),
      });
      if (!response.ok) {
        const failure = await response.json().catch(() => null);
        setError(failure?.message ?? "Не удалось сохранить комментарий. Проверьте дату и текст.");
        return;
      }
      attempt.current = null;
      form.reset();
      setSaved(true);
      onSaved();
    } catch {
      setError("Не удалось подтвердить сохранение. Повторите отправку с теми же данными.");
    } finally {
      setSaving(false);
    }
  }

  return <details>
    <summary>Добавить прошлый комментарий</summary>
    <form className="call-form" onSubmit={save}>
      <fieldset disabled={saving}>
        <label>Дата и время события (МСК)<input type="datetime-local" name="occurred" required /></label>
        <label>Текст комментария<textarea name="comment" maxLength={4000} required /></label>
        <DocumentQuarterField />
        {error && <p role="alert">{error}</p>}
        {saved && <p role="status">Комментарий сохранён</p>}
        <button className="button primary" type="submit">
          {saving ? "Сохраняем…" : "Сохранить комментарий"}
        </button>
      </fieldset>
    </form>
  </details>;
}

function formatMoscow(timestamp: string) {
  return new Date(timestamp).toLocaleString("ru-RU", {
    dateStyle: "short", timeStyle: "short", timeZone: "Europe/Moscow",
  }) + " мск";
}

type ClientEvent = {
  id: string;
  created_at: string;
  historical_event_date?: string | null;
  author_name: string;
  call_result?: CallResult | null;
  comment_text: string;
  document_quarters?: string[];
  promised_date?: string | null;
  wait_until?: string | null;
};

export function ClientHistory({ code, allowComments = false }: { code: string; allowComments?: boolean }) {
  const [items, setItems] = useState<ClientEvent[]>([]);
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/clients/" + encodeURIComponent(code) + "/events?page=" + page,
      { signal: controller.signal })
      .then(async response => {
        if (!response.ok) throw new Error();
        const history = await response.json();
        if (controller.signal.aborted) return;
        setItems(history.items);
        setPageCount(Math.max(1, Math.ceil(history.total / history.page_size)));
      })
      .catch(() => { if (!controller.signal.aborted) setError("Не удалось загрузить историю."); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [code, page, reload]);

  function changePage(next: number) {
    setLoading(true);
    setError("");
    setPage(next);
  }

  return <section>
    <h3>Лента событий</h3>
    {allowComments && <HistoricalCommentForm code={code} onSaved={() => {
      changePage(1);
      setReload(value => value + 1);
    }}/>}
    {loading ? <p>Загрузка истории…</p> : error ? <>
      <p role="alert">{error}</p>
      <button className="button secondary" onClick={() => {
        setError(""); setLoading(true); setReload(value => value + 1);
      }}>Повторить</button>
    </> : <>
      {!items.length && <p>Событий пока нет.</p>}
      {items.map(item => <div className="status-row" key={item.id}>
        {item.historical_event_date && <>
          <b>Прошлый комментарий</b>
          <small>Событие {formatMoscow(item.historical_event_date)}</small>
        </>}
        <small>{item.historical_event_date ? "Внесено " : ""}{formatMoscow(item.created_at)}
          {" · "}{item.author_name}</small>
        {item.call_result && <b>{results[item.call_result]}</b>}
        {item.comment_text && <span>{item.comment_text}</span>}
        {!!item.document_quarters?.length && <small>Документы за {item.document_quarters.join(", ")}</small>}
        {item.promised_date && <small>Обещанная дата передачи {item.promised_date}</small>}
        {item.wait_until && <small>Ожидание до {item.wait_until}</small>}
      </div>)}
      {pageCount > 1 && <div>
        <button className="button secondary" disabled={page <= 1}
          onClick={() => changePage(page - 1)}>Предыдущая</button>
        <span> {page} / {pageCount} </span>
        <button className="button secondary" disabled={page >= pageCount}
          onClick={() => changePage(page + 1)}>Следующая</button>
      </div>}
    </>}
  </section>;
}
