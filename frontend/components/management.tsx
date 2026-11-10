'use client';
import { useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { queryString, request } from '@/lib/api';
import type { ImportBatch, User, UserListResponse, Role } from '@/lib/contracts';
import { Feedback, Pager, QuarterInput, useAction, useResource } from './api-ui';

export function Users() {
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const result = useResource<UserListResponse>('/users' + queryString({ page, page_size: 20 }), revision);
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('OPERATOR');
  const action = useAction();
  return <><h1>Пользователи и роли</h1><Feedback {...result} /><button className="text-link" onClick={() => setRevision(revision + 1)}>Обновить</button>{result.data && <><div className="table-wrap"><table><thead><tr><th>Логин</th><th>Роль</th><th>Статус</th></tr></thead><tbody>{result.data.items.map(user => <tr key={user.id}><td>{user.login}</td><td>{user.role === 'ADMIN' ? 'Администратор' : 'Оператор'}</td><td>{user.is_active ? 'Активен' : 'Отключён'}</td></tr>)}</tbody></table></div><Pager page={page} total={result.data.total} onChange={setPage} /></>}<form className="call-form" onSubmit={e => { e.preventDefault(); void action.run(async () => {
    await request<User>('/users', { method: 'POST', body: JSON.stringify({ login, password, role }) });
    setLogin(''); setPassword(''); setPage(1); setRevision(revision + 1); action.setNotice('Пользователь создан');
  }); }}><h2>Добавить пользователя</h2><label>Логин<input required autoComplete="off" value={login} onChange={e => setLogin(e.target.value)} /></label><label>Пароль<input required type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} /></label><label>Роль<select value={role} onChange={e => setRole(e.target.value as Role)}><option value="OPERATOR">Оператор</option><option value="ADMIN">Администратор</option></select></label><Feedback {...action} /><button className="button primary" disabled={action.busy}>Создать пользователя</button></form></>;
}
export function Imports({ reportOnly }: { reportOnly: boolean }) {
  const router = useRouter();
  const params = useSearchParams();
  const batchId = params.get('batch') || '';
  const [id, setId] = useState(batchId);
  const [file, setFile] = useState<File | null>(null);
  const [quarter, setQuarter] = useState('');
  const [date, setDate] = useState('');
  const [type, setType] = useState('REGISTRY');
  const [revision, setRevision] = useState(0);
  const action = useAction();
  const operation = useRef<Record<string, string>>({});
  const report = useResource<ImportBatch>(batchId ? `/imports/${encodeURIComponent(batchId)}` : null, revision, true);
  return <><h1>{reportOnly ? 'Отчёт об импорте' : 'Загрузка реестра'}</h1>{!reportOnly && <form className="call-form" onSubmit={e => { e.preventDefault(); void action.run(async () => {
    if (!file || !file.name.toLowerCase().endsWith('.xlsx')) throw new Error('Выберите файл .xlsx');
    const data = new FormData(); data.set('file', file); data.set('selected_quarter', quarter); data.set('import_type', type); data.set('snapshot_date', date);
    const batch = await request<ImportBatch>('/imports', { method: 'POST', body: data });
    router.push(`/import-report?batch=${encodeURIComponent(batch.id)}`);
  }); }}><label className="upload-box">Файл реестра .xlsx<input required type="file" accept=".xlsx" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label><label>Квартал загрузки<QuarterInput required value={quarter} onChange={setQuarter} /></label><label>Дата среза<input required type="date" value={date} onChange={e => setDate(e.target.value)} /></label><label>Тип импорта<select value={type} onChange={e => setType(e.target.value)}><option value="REGISTRY">Реестр</option><option value="SNAPSHOT">Срез</option></select></label><button className="button primary" disabled={action.busy}>Загрузить</button></form>}<Feedback {...action} />
    <form onSubmit={e => { e.preventDefault(); router.push(`/import-report?batch=${encodeURIComponent(id.trim())}`); }}><label>ID пакета<input required value={id} onChange={e => setId(e.target.value)} /></label><button className="button secondary">Открыть отчёт</button></form><Feedback {...report} />{batchId && <button className="text-link" onClick={() => setRevision(revision + 1)}>Обновить отчёт</button>}{report.data && <><h2>{report.data.filename}</h2><p>{report.data.selected_quarter} · {report.data.snapshot_date} · {report.data.status}</p><div className="stats-grid">{[['Строк в файле', report.data.total_rows], ['Принято', report.data.success_count], ['Ошибок', report.data.error_count]].map(([label, value]) => <div className="stat-card" key={label}><small>{label}</small><b>{value}</b></div>)}</div>{report.data.status === 'COMPLETED' && <button className="button secondary" disabled={action.busy} onClick={() => {
      if (!window.confirm(`Откатить пакет ${report.data!.filename}? Сервер проверит, что это последний применённый пакет.`)) return;
      void action.run(async () => {
        const operation_id = operation.current[batchId] ||= crypto.randomUUID();
        await request<ImportBatch>(`/imports/${encodeURIComponent(batchId)}/rollback`, { method: 'POST', body: JSON.stringify({ operation_id }) });
        delete operation.current[batchId]; setRevision(revision + 1); action.setNotice('Пакет откачен');
      });
    }}>Откатить пакет</button>}</>}</>;
}
