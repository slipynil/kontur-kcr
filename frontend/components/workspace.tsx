"use client";
import { useEffect, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";
import ReassignmentNotifications from "./reassignment-notifications";
import { CallForm, ClientHistory } from "./client-events";

type Client={code:string;name:string;count:number;delta:string;trend:string;stage:string;date:string;owner:string;tag?:string};
type AssignmentView = { code: string; owner: string | null; operatorWork?: boolean; published?: boolean; assignmentId?: string | null; ownerIsMe?: boolean; workQuarter?: string; stage?: string | null; waitUntil?: string | null; awaitingBalance?: boolean };
type UserRow={username:string;name:string;role:"Администратор"|"Оператор";active:boolean;clients:number};
const seed:Client[]=[
{code:"AAA00412",name:"ООО «Северный терминал»",count:37,delta:"+12",trend:"Растёт",stage:"Второй звонок",date:"24.10",owner:"Свободен",tag:"VIP"},
{code:"AAA02210",name:"ИП Гаврилов С. А.",count:21,delta:"0",trend:"Без изменений",stage:"Претензия",date:"23.10",owner:"Анна К."},
{code:"AAA01877",name:"нет названия в реестре",count:14,delta:"+14",trend:"Впервые",stage:"Нет этапа",date:"24.10",owner:"Свободен"},
{code:"AAA03345",name:"ПАО «Ладога-Сервис»",count:9,delta:"+9",trend:"Повторно",stage:"Первый звонок",date:"22.10",owner:"Анна К."},
{code:"AAA00981",name:"АО «Техснаб»",count:8,delta:"−6",trend:"Уменьшается",stage:"Ожидание срока",date:"24.10",owner:"Анна К."},
{code:"AAA00560",name:"ООО «Волга Ком»",count:6,delta:"−1",trend:"Уменьшается",stage:"Ожидание срока",date:"24.10",owner:"Анна К.",tag:"Интегратор"},
{code:"AAA00073",name:"ООО «Интегра Плюс»",count:0,delta:"−5",trend:"Все переданы",stage:"Цикл завершён",date:"—",owner:"Свободен",tag:"Интегратор"},
{code:"AAA04012",name:"АО «Балтпорт»",count:0,delta:"0",trend:"Все переданы",stage:"Нет этапа",date:"—",owner:"Свободен",tag:"VIP"}];
type Page="Клиенты"|"Загрузка реестра"|"Отчёт об импорте"|"Список на обзвон"|"Сегодня нужно"|"Карточка клиента"|"Звонок"|"Пользователи"|"Справочник статусов";
const paths:Record<Page,string>={"Клиенты":"/","Загрузка реестра":"/upload","Отчёт об импорте":"/import-report","Список на обзвон":"/call-list","Сегодня нужно":"/today","Карточка клиента":"/client","Звонок":"/call","Пользователи":"/users","Справочник статусов":"/statuses"};
const pageFromPath=(path:string):Page=>path.startsWith("/client/")?"Карточка клиента":path.startsWith("/call/")?"Звонок":({"/":"Клиенты","/upload":"Загрузка реестра","/import-report":"Отчёт об импорте","/call-list":"Список на обзвон","/today":"Сегодня нужно","/client":"Карточка клиента","/call":"Звонок","/users":"Пользователи","/statuses":"Справочник статусов"}[path] as Page)||"Клиенты";
export default function Workspace(){
const router=useRouter();
const pathname=usePathname();
const routeClientCode=pathname.match(/^\/(?:client|call)\/([^/]+)/)?.[1];
const initialCode=routeClientCode?decodeURIComponent(routeClientCode):undefined;
const page=pageFromPath(pathname); const [role,setRole]=useState<"Администратор"|"Оператор">("Администратор"),[query,setQuery]=useState(""),[userRows,setUserRows]=useState<UserRow[]>([]),[addUserOpen,setAddUserOpen]=useState(false),[newUsername,setNewUsername]=useState(""),[newName,setNewName]=useState(""),[newRole,setNewRole]=useState<"Администратор"|"Оператор">("Оператор"),[newPassword,setNewPassword]=useState(""),[userError,setUserError]=useState(""),[periodFilter,setPeriodFilter]=useState("2025Q1–2026Q2"),[trendFilter,setTrendFilter]=useState("Все динамики"),[stageFilter,setStageFilter]=useState("Все этапы"),[ownerFilter,setOwnerFilter]=useState("Все ответственные"),[tagFilter,setTagFilter]=useState("Все признаки"),[selectedClient,setClient]=useState((seed.find(x=>x.code===initialCode)||{code:initialCode||seed[0].code,name:initialCode?"Клиент не найден":seed[0].name,count:0,delta:"0",trend:"—",stage:"—",date:"—",owner:"Свободен"})),[file,setFile]=useState(""),[toast,setToast]=useState(""),[signed,setSigned]=useState(false),[authReady,setAuthReady]=useState(false),[owner,setOwner]=useState("Загрузка…"),[username,setUsername]=useState("vera.v"),[password,setPassword]=useState("1234"),[loginError,setLoginError]=useState("");
const client = initialCode ? seed.find(item => item.code === initialCode) ??
  { ...selectedClient, code: initialCode, name: "Клиент не найден", count: 0, tag: undefined }
  : selectedClient;
const [assignment, setAssignment] = useState<AssignmentView | null>(null);
const [taking, setTaking] = useState(false);
const [publishedPool, setPublishedPool] = useState<{ code: string; owner: string }[] | null>([]);
const requestAttempt = useRef<{ code: string; expected: string | null; id: string } | null>(null);
const currentAssignment = assignment?.code === client.code ? assignment : null;
const stageLabels: Record<string, string> = {
  POOL: "В пуле", IN_WORK: "В работе", WAIT_DEADLINE: "Ожидание срока",
  CLAIM: "Претензия", CLOSED: "Цикл завершён",
};
const notify=(s:string)=>{setToast(s);setTimeout(()=>setToast(""),2500)};
useEffect(()=>{fetch("/api/auth").then(async r=>{if(r.ok){const data=await r.json();setRole(data.user.role);setSigned(true);sessionStorage.setItem("kontur-role",data.user.role)}else{sessionStorage.removeItem("kontur-role");setSigned(false)}}).catch(()=>{}).finally(()=>setAuthReady(true));},[]);
useEffect(()=>{if(page!=="Пользователи"||role!=="Администратор")return;fetch("/api/users").then(r=>r.ok?r.json():null).then(data=>{if(data)setUserRows(data.users)}).catch(()=>{})},[page,role]);

const go=(p:Page)=>{if(paths[p])router.push(paths[p])};
useEffect(() => {
  if (!signed || (page !== "Карточка клиента" && page !== "Звонок")) return;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  async function refresh() {
    let retry = true;
    try {
      const response = await fetch("/api/clients/" + encodeURIComponent(client.code),
        { signal: controller.signal });
      if (response.ok) {
        const data: AssignmentView = await response.json();
        if (controller.signal.aborted) return;
        setAssignment({ ...data, code: client.code });
        setOwner(data.owner || "Свободен");
        retry = Boolean(data.operatorWork);
      }
    } catch {
      // Retry when the service becomes reachable again.
    } finally {
      if (retry && !controller.signal.aborted) timer = setTimeout(refresh, 5000);
    }
  }
  void refresh();
  return () => {
    controller.abort();
    clearTimeout(timer);
  };
}, [signed, page, client.code]);

async function takeClient() {
  if (!currentAssignment || taking) return;
  if (currentAssignment.ownerIsMe) {
    router.push("/call/" + client.code);
    return;
  }
  setTaking(true);
  try {
    const expected = currentAssignment.assignmentId ?? null;
    if (requestAttempt.current?.code !== client.code || requestAttempt.current.expected !== expected) {
      requestAttempt.current = { code: client.code, expected, id: crypto.randomUUID() };
    }
    const response = await fetch("/api/clients/" + encodeURIComponent(client.code) + "/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_assignment_id: expected,
        request_id: requestAttempt.current.id,
      }),
    });
    const data = await response.json();
    if (response.status < 500) requestAttempt.current = null;
    if (response.status === 202) {
      notify("Запрос отправлен. У ответственного есть час, чтобы отказаться.");
    } else if (response.ok && (!data.status || data.status === "TRANSFERRED")) {
      if (data.owner) setOwner(data.owner);
      notify("Клиент взят в работу");
      router.push("/call/" + client.code);
    } else {
      notify(data.message || data.error || "Запрос уже завершён");
    }
  } catch {
    notify("Не удалось отправить запрос. Повторите попытку.");
  } finally {
    setTaking(false);
  }
}

useEffect(()=>{if(!authReady||!signed||role!=="Оператор")return;const allowed=pathname==="/"||pathname==="/today"||pathname.startsWith("/client/")||pathname.startsWith("/call/");if(!allowed)router.replace("/today")},[authReady,signed,role,pathname,router]);
useEffect(() => {
  if (!signed || role !== "Оператор") return;
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout>;
  async function refreshPool() {
    try {
      const response = await fetch("/api/operator-work?pool", { signal: controller.signal });
      if (response.ok) {
        const data = await response.json();
        if (controller.signal.aborted) return;
        if (!data.enabled) {
          setPublishedPool(null);
          return;
        }
        setPublishedPool(data.items);
      }
    } catch {
      // Retry transient failures without replacing the last published pool.
    }
    if (!controller.signal.aborted) timer = setTimeout(refreshPool, 5000);
  }
  void refreshPool();
  return () => {
    controller.abort();
    clearTimeout(timer);
  };
}, [signed, role]);

const poolOwners = new Map(publishedPool?.map(item => [item.code, item.owner]));
const visibleClients = role === "Оператор" && publishedPool !== null
  ? seed.filter(item => poolOwners.has(item.code))
    .map(item => ({ ...item, owner: poolOwners.get(item.code) ?? item.owner }))
  : seed;
const inPeriod=(code:string)=>periodFilter==="2025Q1–2026Q2"|| (periodFilter==="2025 год"&&["AAA00412","AAA02210","AAA03345","AAA00981","AAA00073"].includes(code)) || (periodFilter==="2026 год"&&["AAA00412","AAA01877","AAA00560","AAA00073","AAA04012"].includes(code));
const rows=visibleClients.filter(x=>inPeriod(x.code)&&(x.code+x.name).toLowerCase().includes(query.toLowerCase())&&(trendFilter==="Все динамики"||x.trend===trendFilter)&&(stageFilter==="Все этапы"||x.stage===stageFilter)&&(ownerFilter==="Все ответственные"||x.owner===ownerFilter)&&(tagFilter==="Все признаки"||(tagFilter==="Без признака"?!x.tag:x.tag===tagFilter)));
if(!authReady)return <main className="auth-loading" aria-label="Загрузка"/>;
if(!signed)return <main className="login-page"><form className="login-card" onSubmit={async e=>{e.preventDefault();setLoginError("");const res=await fetch("/api/auth",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username,password})});if(res.ok){const data=await res.json();setRole(data.user.role);go(data.user.role==="Оператор"?"Сегодня нужно":"Клиенты");setSigned(true)}else setLoginError("Неверный логин или пароль")}}><div className="login-head"><div><h1>Контур КЦР</h1><p>Контроль передачи оригиналов документов</p></div></div><label>Логин<input value={username} onChange={e=>setUsername(e.target.value)}/></label><label>Пароль<input type="password" value={password} onChange={e=>setPassword(e.target.value)}/></label>{loginError&&<p className="overdue">{loginError}</p>}<button className="button login-button">Войти</button><small>После входа пользователь видит только разделы своей роли: администратора или оператора.</small></form></main>;
const admin=["Клиенты","Загрузка реестра","Список на обзвон","Пользователи","Справочник статусов"] as Page[];
const operator=["Сегодня нужно","Клиенты"] as Page[];
return <main className="app-shell"><aside className="sidebar"><div className="brand"><span className="brand-mark">К</span><span><b>Контур КЦР</b><small>{role} · {role==="Администратор"?"Вера В.":"Анна К."}</small></span></div>{(role==="Администратор"?admin:operator).map((n,i)=><button key={n} className={"nav-item "+(page===n?"selected":"")} onClick={()=>go(n)}><span className="nav-icon">{["▦","⇧","☷","♙","◇"][i]}</span>{n}</button>)}<button className="logout" onClick={async()=>{await fetch("/api/auth",{method:"DELETE"});sessionStorage.removeItem("kontur-role");setPublishedPool([]);setSigned(false)}}>Выйти</button></aside><section className="workspace"><header className="topbar"><span>Контур КЦР　/　{page}</span><span className="muted">● Все изменения сохранены</span></header><div className="content">
{role==="Оператор"&&<ReassignmentNotifications/>}
{page==="Клиенты"&&<><div className="page-heading"><div><h1>Клиенты </h1></div>{role==="Администратор"&&<button className="button primary" onClick={()=>go("Загрузка реестра")}>Загрузить реестр</button>}</div><input className="wide-search" placeholder="Код КЦР или ИНН, например AAA00412" value={query} onChange={e=>setQuery(e.target.value)}/><div className="filter-line"><FilterSelect label="Период" value={periodFilter} options={["2025Q1–2026Q2","2026 год","2025 год"]} onChange={setPeriodFilter}/><FilterSelect label="Динамика" value={trendFilter} options={["Все динамики",...Array.from(new Set(seed.map(x=>x.trend)))]} onChange={setTrendFilter}/><FilterSelect label="Этап" value={stageFilter} options={["Все этапы",...Array.from(new Set(seed.map(x=>x.stage)))]} onChange={setStageFilter}/><FilterSelect label="Ответственный" value={ownerFilter} options={["Все ответственные",...Array.from(new Set(seed.map(x=>x.owner)))]} onChange={setOwnerFilter}/><FilterSelect label="Признаки" value={tagFilter} options={["Все признаки","VIP","Интегратор","Без признака"]} onChange={setTagFilter}/><button className="filter-reset" onClick={()=>{setQuery("");setPeriodFilter("2025Q1–2026Q2");setTrendFilter("Все динамики");setStageFilter("Все этапы");setOwnerFilter("Все ответственные");setTagFilter("Все признаки")}}>Сбросить</button></div><label className="checkbox-line"><input type="checkbox" defaultChecked/> Скрыть «Не нужно работать»</label><Table rows={rows} open={x=>{setClient(x);router.push(`/client/${x.code}`)}}/></>}
{page==="Загрузка реестра"&&<><h1>Загрузка реестра </h1><p>Клиенты и срезы приходят одним файлом. Повторная загрузка того же файла данные не меняет.</p><label className="upload-box">Перетащите файл реестра .xlsx<small>или выберите на компьютере · до 50 МБ</small><input type="file" accept=".xlsx" onChange={e=>setFile(e.target.files?.[0]?.name||"")}/>{file&&<span className="file-chip">{file}</span>}</label><button className="button primary" onClick={()=>go("Отчёт об импорте")}>Загрузить</button> <button className="button secondary" onClick={()=>setFile("")}>Отменить</button><h2 className="section-title">Последние загрузки</h2><table><thead><tr>{["Дата","Файл","Кто","Срезов принято","Отклонено ячеек","Результат"].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{["22.10.2026 11:20　 Все_КЦР_22-10-2026.xlsx　 Вера В.　 0　 0　 Файл уже загружали, данные не изменены","22.10.2026 11:04　 Все_КЦР_22-10-2026.xlsx　 Вера В.　 74 812　 584　 Загружен","15.10.2026 09:47　 Все_КЦР_15-10-2026.xlsx　 Вера В.　 73 905　 612　 Загружен"].map(x=><tr key={x}><td colSpan={6}>{x}</td></tr>)}</tbody></table></>}
{page==="Отчёт об импорте"&&<><button className="back-link" onClick={()=>go("Загрузка реестра")}>← Загрузка реестра</button><h1>Отчёт об импорте </h1><p>{file||"Все_КЦР_24-10-2026.xlsx"} · новый срез на 24.10.2026 · загружен за 18 с</p><div className="stats-grid">{[["Клиентов в файле","5 874"],["Срезов принято","75 210"],["Новых клиентов","12"],["Отклонено ячеек","584"],["Конфликты, не перезаписаны","3"]].map(x=><div className="stat-card" key={x[0]}><small>{x[0]}</small><b>{x[1]}</b></div>)}</div><div className="two-col"><section><h2>Динамика к прошлому срезу </h2>{["Впервые　312","Повторно　87","Растёт　236","Уменьшается　1 140","Без изменений　2 980","Все переданы　905"].map(x=><div className="status-row" key={x}>{x}</div>)}<button className="text-link" onClick={()=>go("Клиенты")}>Открыть в таблице клиентов →</button><p><button className="button primary" onClick={()=>go("Список на обзвон")}>Сформировать список на обзвон</button></p></section><section><h2>Ошибки и предупреждения</h2>{["ВН, стр. 214　AAA01234　Дробный долг 2,5","стр. 3051　#N/A　Неизвестный код КЦР","срез 24.10　AAA00981　Нет «Всего документов»","срез 22.10　AAA00412　Конфликт, старое сохранено"].map(x=><div className="status-row" key={x}>{x}</div>)}<small>и ещё 583 ячейки · скачать полный список</small></section></div></>}
{page==="Список на обзвон"&&role==="Администратор"&&<PoolPublication clients={seed}/>}
{page==="Сегодня нужно"&&<><h1>Сегодня нужно </h1><p>24 октября · у вас в работе 6 клиентов · просроченные всегда сверху</p><h3 className="overdue">Просрочено · 2</h3><Table rows={rows.slice(3,5)} open={x=>{setClient(x);router.push(`/client/${x.code}`)}}/><h3>Сегодня · 4</h3><Table rows={rows.slice(0,4)} open={x=>{setClient(x);router.push(`/client/${x.code}`)}}/><h3>Свободные из списка на обзвон · 14</h3><Table rows={rows.slice(0,3)} open={x=>{setClient(x);router.push(`/client/${x.code}`)}}/></>}
{page==="Карточка клиента"&&<><button className="back-link" onClick={()=>go(role==="Оператор"?"Сегодня нужно":"Клиенты")}>← {role==="Оператор"?"Сегодня нужно":"Клиенты"}</button><div className="page-heading"><h1>{client.code} · {client.name} </h1>{role==="Оператор"&&<button className="button primary"
  disabled={taking||!currentAssignment||currentAssignment.published===false||currentAssignment.awaitingBalance||
    (!currentAssignment.operatorWork&&owner!=="Свободен")}
  onClick={()=>void takeClient()}>
  {!currentAssignment ? "Загрузка…" : currentAssignment.published===false ? "Клиент вне пула" :
    currentAssignment.awaitingBalance ? "Срок истёк · вне пула" :
    currentAssignment.ownerIsMe ? "Открыть звонок" : owner==="Свободен" ? "Взять в работу" :
    currentAssignment.operatorWork ? "Перезакрепить на себя" : "Клиент занят"}
</button>}</div><p>ИНН нет в реестре · {client.tag||"действующий"}</p><div className="stats-grid">{[["Не передано",String(client.count)],["Динамика",client.trend],["Этап",currentAssignment?.operatorWork ? stageLabels[currentAssignment.stage ?? ""] ?? "—" : client.stage],["Ожидание до",currentAssignment?.operatorWork ? currentAssignment.waitUntil ?? "—" : client.date],["Ответственный",currentAssignment ? owner : "Загрузка…"]].map(x=><div className="stat-card" key={x[0]}><small>{x[0]}</small><b>{x[1]}</b></div>)}</div>{role==="Оператор"&&<div className="history-grid"><section><h3>История срезов</h3>{["22.10　нет　37　Растёт","15.10　145　25　Уменьшается","08.10　142　31　Растёт","01.10　140　20　Впервые"].map(x=><div className="status-row" key={x}>{x}</div>)}</section>{currentAssignment?.operatorWork ? <ClientHistory key={client.code} code={client.code} allowComments={currentAssignment.published}/> : <section><h3>Лента событий</h3>{["24.10 · импорт　Срез 22.10: не передано 37, на 12 больше","16.10 · звонок · Анна К.　Обещали передать 25 документов до 23.10","08.10 · звонок · Анна К.　Недозвон, следующий контакт 10.10"].map(x=><div className="status-row" key={x}>{x}</div>)}</section>}<section className="panel rules"><h3>ИИ-помощник </h3><button className="button primary" onClick={()=>notify("Сводка подготовлена")}>Как менялся долг в октябре?</button><p>Непереданных стало больше: 20 на 01.10 и 37 на 22.10. Нужен второй звонок.</p></section></div>}{role==="Администратор"&&currentAssignment?.operatorWork&&currentAssignment.published&&<ClientHistory key={client.code} code={client.code}/>}{role==="Администратор"&&!currentAssignment?.operatorWork&&owner!=="Свободен"&&owner!=="Загрузка…"&&<div className="two-col"><section><h3>Что видит оператор</h3><p>Кнопка неактивна, если клиента уже взял другой оператор. Карточку можно читать, но звонок записать нельзя.</p></section><section className="panel rules"><small>Вид администратора</small><h2>Снять клиента с оператора</h2><label>Оператор<input value={owner} onChange={e=>setOwner(e.target.value)}/></label><label>Причина *<textarea defaultValue="Олег на больничном до 30.10, клиенту сегодня нужен звонок"/></label><small>Причина попадёт в ленту событий клиента</small><p><button className="button primary" onClick={async()=>{const res=await fetch(`/api/clients/${client.code}`,{method:"DELETE"});if(res.ok){setOwner("Свободен");notify("Клиент снят с оператора")}else notify("Не удалось снять клиента")}}>Снять с оператора</button></p></section></div>}</>}
{page==="Звонок"&&<>
  <button className="back-link" onClick={()=>router.push("/client/"+client.code)}>← Карточка клиента</button>
  <h1>Звонок · {client.code} · {client.name}</h1>
  {currentAssignment?.operatorWork && role==="Оператор" ?
    <CallForm key={client.code} code={client.code} assignment={currentAssignment}
      onSaved={message=>{notify(message);router.push("/client/"+client.code)}}/> :
    <p>{currentAssignment ? "Для сохранения звонка требуется доступ к Operator Work." : "Загружается назначение клиента…"}</p>}
</>}
{page==="Пользователи"&&<><div className="page-heading"><h1>Пользователи и роли </h1><button className="button primary" onClick={()=>{setUserError("");setAddUserOpen(true)}}>Добавить пользователя</button></div><p>Оператор видит только «Сегодня нужно» и «Клиенты». Администратор видит всё.</p><div className="table-wrap"><table><thead><tr>{["Логин","Имя","Роль","Клиентов в работе","Статус"].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{userRows.map(user=><tr key={user.username}><td><b>{user.username}</b></td><td>{user.name}</td><td><FilterSelect label="" value={user.role} options={["Администратор","Оператор"]} onChange={value=>setUserRows(rows=>rows.map(row=>row.username===user.username?{...row,role:value as UserRow["role"]}:row))}/></td><td>{user.clients||"нет"}</td><td><span className={user.active?"user-status active-status":"user-status"}>{user.active?"Активен":"Отключён"}</span></td></tr>)}</tbody></table></div></>}
{page==="Справочник статусов"&&<><h1>Справочник статусов</h1><p>Этапы и причины работы с клиентами</p>{["Первый звонок","Второй звонок","Ожидание срока","Претензия","Цикл завершён"].map(x=><div className="status-row" key={x}>{x}<button className="button secondary" onClick={()=>notify("Редактирование статуса")}>Изменить</button></div>)}</>}
</div></section><>{addUserOpen&&<div className="modal-backdrop" onMouseDown={e=>{if(e.target===e.currentTarget)setAddUserOpen(false)}}><form className="add-user-modal" onSubmit={async e=>{e.preventDefault();setUserError("");const res=await fetch("/api/users",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({username:newUsername,name:newName,role:newRole,password:newPassword})});const data=await res.json();if(!res.ok){setUserError(data.error||"Не удалось добавить пользователя");return}setUserRows(rows=>[...rows,data.user]);setAddUserOpen(false);setNewUsername("");setNewName("");setNewPassword("");notify("Пользователь добавлен")}}><button type="button" className="modal-x" onClick={()=>setAddUserOpen(false)}>×</button><span className="modal-kicker">НОВАЯ УЧЁТНАЯ ЗАПИСЬ</span><h2>Добавить пользователя</h2><p>Заполните данные для входа и выберите роль.</p><label>Логин<input required minLength={3} maxLength={32} pattern="[A-Za-z0-9._-]+" value={newUsername} onChange={e=>setNewUsername(e.target.value)} placeholder="например, ivan.s"/></label><label>Имя пользователя<input required minLength={2} maxLength={80} value={newName} onChange={e=>setNewName(e.target.value)} placeholder="Иван С."/></label><label>Роль<FilterSelect label="" value={newRole} options={["Оператор","Администратор"]} onChange={value=>setNewRole(value as UserRow["role"])}/></label><label>Пароль<input required minLength={4} maxLength={128} type="password" value={newPassword} onChange={e=>setNewPassword(e.target.value)} placeholder="Не менее 4 символов"/></label>{userError&&<div className="form-error">{userError}</div>}<div className="modal-actions"><button type="button" className="button secondary" onClick={()=>setAddUserOpen(false)}>Отмена</button><button className="button primary">Создать пользователя</button></div></form></div>}</>{toast&&<div className="toast">{toast}</div>}</main>;
}
function FilterSelect({label,value,options,onChange}:{label:string;value:string;options:string[];onChange:(value:string)=>void}){
const [open,setOpen]=useState(false);const root=useRef<HTMLDivElement>(null);
useEffect(()=>{if(!open)return;const close=(event:MouseEvent)=>{if(!root.current?.contains(event.target as Node))setOpen(false)};const escape=(event:KeyboardEvent)=>{if(event.key==="Escape")setOpen(false)};document.addEventListener("mousedown",close);document.addEventListener("keydown",escape);return()=>{document.removeEventListener("mousedown",close);document.removeEventListener("keydown",escape)}},[open]);
return <div className={`filter-select-wrap ${label?"":"compact-select"}`} ref={root}>{label&&<span className="filter-label">{label}</span>}<button type="button" className={`filter-trigger ${open?"is-open":""}`} aria-haspopup="listbox" aria-expanded={open} onClick={()=>setOpen(x=>!x)}><span>{value}</span><svg viewBox="0 0 16 16" aria-hidden="true"><path d="m4 6 4 4 4-4"/></svg></button>{open&&<div className="filter-menu" role="listbox" aria-label={label||"Выбор роли"}>{options.map(option=><button type="button" role="option" aria-selected={option===value} className={`filter-option ${option===value?"chosen":""}`} key={option} onClick={()=>{onChange(option);setOpen(false)}}><span>{option}</span>{option===value&&<span className="option-check">✓</span>}</button>)}</div>}</div>
}
function PoolPublication({ clients }: { clients: Client[] }) {
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  function toggle(code: string) {
    setError("");
    setMessage("");
    setSelected(previous => {
      const next = new Set(previous);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  }

  async function publish() {
    if (publishing || !selected.size) return;
    setPublishing(true);
    setError("");
    setMessage("");
    try {
      const response = await fetch("/api/operator-work", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codes: [...selected] }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        setError(data?.message ?? "Не удалось подтвердить публикацию. Повторите отправку.");
        return;
      }
      setSelected(new Set());
      setMessage("Список опубликован");
    } catch {
      setError("Не удалось подтвердить публикацию. Повторите отправку.");
    } finally {
      setPublishing(false);
    }
  }

  return <section>
    <h1>Список на обзвон</h1>
    <p>Выберите клиентов, которых нужно передать операторам.</p>
    <Table rows={clients} selection={{ selected, toggle, disabled: publishing }}/>
    <p>Выбрано клиентов: {selected.size}</p>
    <button type="button" className="button primary" disabled={publishing || !selected.size}
      onClick={() => void publish()}>
      {publishing ? "Публикуем…" : "Передать операторам"}
    </button>
    {message && <p role="status">{message}</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}

function Table({ rows, open, selection }: {
  rows: Client[];
  open?: (client: Client) => void;
  selection?: { selected: Set<string>; toggle: (code: string) => void; disabled: boolean };
}) {
  return <div className="table-wrap"><table>
    <thead><tr>
      {selection && <th scope="col">Выбор</th>}
      {["Код КЦР", "Название", "Не передано", "К прошлому", "Динамика", "Этап",
        "След. контакт", "Ответственный", "Признаки"].map(label => <th key={label}>{label}</th>)}
    </tr></thead>
    <tbody>{rows.map(client => <tr key={client.code} onClick={open ? () => open(client) : undefined}>
      {selection && <td>
        <input type="checkbox" aria-label={"Выбрать " + client.code}
          checked={selection.selected.has(client.code)} disabled={selection.disabled}
          onChange={() => selection.toggle(client.code)} />
      </td>}
      <td className="blue-text">{client.code}</td>
      <td>{client.name}</td>
      <td><b>{client.count}</b></td>
      <td>{client.delta}</td>
      <td><span className="pill">{client.trend}</span></td>
      <td>{client.stage}</td>
      <td>{client.date}</td>
      <td>{client.owner}</td>
      <td>{client.tag}</td>
    </tr>)}</tbody>
  </table></div>;
}
