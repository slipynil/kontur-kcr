'use client';

import { ChevronRight, FileSpreadsheet, Plus, RefreshCw, Upload, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { newRequestId, queryString, readSession, request } from '@/lib/api';
import { includeImport, readImportHistory, rememberImport } from '@/lib/import-history';
import type { ImportBatch, User, UserListResponse, Role } from '@/lib/contracts';
import { DateField } from './date-field';
import { displayDate, Feedback, Pager, QuarterInput, quarterName, quarterPattern, useAction, useResource } from './api-ui';
import { Badge, ConfirmActions, EmptyState, Metrics, Modal, PageHeader } from './presentation';

const roleLabels = { ADMIN: 'Администратор', OPERATOR: 'Оператор' };
const importTypes = { REGISTRY: 'Реестр', SNAPSHOT: 'Срез' };
const reportStatus = {
  PROCESSING: ['Обрабатывается', 'caution'],
  COMPLETED: ['Завершён', 'good'],
  ROLLED_BACK: ['Откачен', 'neutral'],
  FAILED: ['Ошибка импорта', 'bad'],
} as const;

export function Users() {
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const result = useResource<UserListResponse>('/users' + queryString({ page, page_size: 20 }), revision);
  const [open, setOpen] = useState(false);
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<Role>('OPERATOR');
  const action = useAction();
  function close() { setOpen(false); setLogin(''); setPassword(''); setRole('OPERATOR'); }

  return <>
    <PageHeader title="Пользователи и роли" description="Учётные записи администраторов и операторов" />
    <div className="section-header">
      <h2>Список пользователей</h2>
      <div className="inline-actions">
        <button type="button" className="button text-link" onClick={() => setRevision(value => value + 1)}><RefreshCw aria-hidden="true" />Обновить</button>
        <button type="button" className="button primary" aria-haspopup="dialog" onClick={() => { action.reset(); setOpen(true); }}>
          <Plus aria-hidden="true" />Добавить пользователя
        </button>
      </div>
    </div>
    <Feedback {...result} />
    {!open && <Feedback notice={action.notice} />}
    {result.data && (result.data.items.length ? <>
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Список пользователей">
        <table><thead><tr><th scope="col">Логин</th><th scope="col">Роль</th><th scope="col">Статус</th></tr></thead>
          <tbody>{result.data.items.map(user => <tr key={user.id}><td>{user.login}</td><td>{roleLabels[user.role]}</td>
            <td><Badge tone={user.is_active ? 'good' : 'neutral'}>{user.is_active ? 'Активен' : 'Отключён'}</Badge></td></tr>)}</tbody>
        </table>
      </div>
      <Pager page={page} total={result.data.total} pageSize={result.data.page_size} onChange={setPage} />
    </> : <EmptyState title="Пользователи не найдены" />)}
    <Modal open={open} title="Добавить пользователя" description="Задайте логин, пароль и роль учётной записи." busy={action.busy} onClose={close}>
      <form className="stack" noValidate onSubmit={event => {
        event.preventDefault();
        void action.run(async () => {
          if (!login.trim()) throw new Error('Введите логин');
          if (!password) throw new Error('Введите пароль');
          await request<User>('/users', { method: 'POST', body: JSON.stringify({ login, password, role }) });
          close(); setPage(1); setRevision(value => value + 1); action.setNotice('Пользователь создан');
        });
      }}>
        <label>Логин<input name="login" required autoComplete="section-new-user username" autoCapitalize="none" spellCheck={false} value={login} onChange={event => setLogin(event.target.value)} /></label>
        <label>Пароль<input name="password" required type="password" autoComplete="section-new-user new-password" value={password} onChange={event => setPassword(event.target.value)} /></label>
        <label>Роль<select name="role" value={role} onChange={event => setRole(event.target.value as Role)}>
          <option value="OPERATOR">Оператор</option><option value="ADMIN">Администратор</option>
        </select></label>
        <Feedback error={action.error} />
        <div className="modal-actions">
          <button type="button" className="button secondary" disabled={action.busy} onClick={close}>Отмена</button>
          <button className="button primary" disabled={action.busy}>Создать пользователя</button>
        </div>
      </form>
    </Modal>
  </>;
}

const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function ImportHistory({ userId, batch }: { userId: string; batch?: ImportBatch }) {
  const [saved] = useState(() => readImportHistory(userId));
  useEffect(() => { if (batch) rememberImport(userId, batch); }, [userId, batch]);
  const entries = batch ? includeImport(saved, batch) : saved;
  return <section aria-label="История отчётов об импорте">
    <div className="section-header"><div><h2>История отчётов</h2>
      <p>Отчёты, открытые в этом браузере.</p></div></div>
    {entries.length ? <div className="table-wrap" role="region" aria-label="Открытые отчёты" tabIndex={0}>
      <table><thead><tr><th scope="col">Файл</th><th scope="col">Квартал загрузки</th><th scope="col">Дата среза</th></tr></thead>
        <tbody>{entries.map(entry => <tr key={entry.id}>
          <td><Link className="text-link report-link" href={`/import-report?batch=${encodeURIComponent(entry.id)}`}>
            {entry.filename}<ChevronRight size={14} aria-hidden="true" />
          </Link></td>
          <td>{quarterName(entry.selected_quarter)}</td><td>{displayDate(entry.snapshot_date)}</td>
        </tr>)}</tbody>
      </table>
    </div> : <EmptyState title="Открытых отчётов пока нет" description="Новые загрузки и открытые отчёты появятся здесь." />}
  </section>;
}

function UploadForm() {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [quarter, setQuarter] = useState('');
  const [date, setDate] = useState('');
  const [type, setType] = useState<ImportBatch['import_type']>('REGISTRY');
  const action = useAction();
  return <>
    <PageHeader title="Загрузка реестра" description="Импортируйте реестр клиентов или квартальный срез из Excel." />
    <div className="section-header"><div><h2>Новый импорт</h2><p>Укажите квартал, к которому относятся данные файла.</p></div></div>
    <form className="panel-form stack" noValidate onSubmit={event => {
      event.preventDefault();
      void action.run(async () => {
        if (!file || !file.name.toLowerCase().endsWith('.xlsx')) throw new Error('Выберите файл .xlsx');
        if (!quarterPattern.test(quarter)) throw new Error('Квартал загрузки должен быть в формате 2026_Q4');
        if (!date) throw new Error('Укажите дату среза');
        const data = new FormData();
        data.set('file', file); data.set('selected_quarter', quarter); data.set('import_type', type); data.set('snapshot_date', date);
        const batch = await request<ImportBatch>('/imports', { method: 'POST', body: data });
        const userId = readSession()?.user.id;
        if (userId) rememberImport(userId, batch);
        router.push(`/import-report?batch=${encodeURIComponent(batch.id)}`);
      });
    }}>
      <div>
        <p className="help-text file-label">Файл Excel</p>
        <div className="upload-row">
          <label className="workbook-upload">
            <input ref={fileInput} required type="file" accept=".xlsx" aria-label="Файл реестра .xlsx"
              disabled={action.busy} onChange={event => { action.reset(); setFile(event.target.files?.[0] ?? null); }} />
            <span className="workbook-icon"><FileSpreadsheet size={21} aria-hidden="true" /></span>
            <span className="workbook-copy" aria-live="polite">
              <strong>{file?.name ?? 'Выбрать файл Excel'}</strong>
              <small>{file ? 'Файл прикреплён · ' + new Intl.NumberFormat('ru', { style: 'unit', unit: 'kilobyte', maximumFractionDigits: 1 }).format(file.size / 1024) : 'Формат .xlsx'}</small>
            </span>
          </label>
          {file && <button type="button" className="button icon-button remove-file" aria-label="Убрать файл" disabled={action.busy}
            onClick={() => { if (fileInput.current) { fileInput.current.value = ''; fileInput.current.focus(); } setFile(null); action.reset(); }}><X aria-hidden="true" /></button>}
        </div>
      </div>
      <div className="form-grid">
        <QuarterInput label="Квартал загрузки" required value={quarter} onChange={setQuarter} />
        <DateField label="Дата среза" value={date} onChange={setDate} required />
        <label>Тип импорта<select value={type} onChange={event => setType(event.target.value as ImportBatch['import_type'])}>
          <option value="REGISTRY">Реестр</option><option value="SNAPSHOT">Срез</option>
        </select></label>
      </div>
      <p className="help-text">Реестр — сведения о клиентах. Срез — состояние документов по кварталам.</p>
      <Feedback error={action.error} loading={action.busy} />
      <div className="form-footer">
        <button className="button primary" disabled={action.busy}><Upload aria-hidden="true" />Загрузить</button>
        <Link className="button secondary" href="/import-report">Отчёт по ID пакета</Link>
      </div>
    </form>
  </>;
}

function ImportReports() {
  const router = useRouter();
  const batchId = useSearchParams().get('batch') || '';
  const userId = readSession()?.user.id;
  const [draft, setDraft] = useState<{ batch: string; value: string }>({ batch: batchId, value: batchId });
  const [idError, setIdError] = useState<{ batch: string; message: string } | null>(null);
  const [revision, setRevision] = useState(0);
  const [rollbackId, setRollbackId] = useState<string | null>(null);
  const action = useAction();
  const operations = useRef<Record<string, string>>({});
  const report = useResource<ImportBatch>(batchId ? `/imports/${encodeURIComponent(batchId)}` : null, revision, true);
  const batch = report.data;
  const showRollback = !!batch && batch.id === rollbackId;
  const id = draft.batch === batchId ? draft.value : batchId;
  function refresh() { setRevision(value => value + 1); }

  function rollback() {
    if (!rollbackId || rollbackId !== batch?.id) return;
    void action.run(async () => {
      const operation_id = operations.current[rollbackId] ||= newRequestId();
      await request<ImportBatch>(`/imports/${encodeURIComponent(rollbackId)}/rollback`, {
        method: 'POST', body: JSON.stringify({ operation_id }),
      });
      delete operations.current[rollbackId];
      setRollbackId(null); refresh(); action.setNotice('Пакет откачен');
    });
  }

  return <>
    <PageHeader title="Отчёт об импорте" description="Откройте отчёт по ID пакета или из истории." />
    <form className="inline-form" noValidate onSubmit={event => {
      event.preventDefault();
      const value = id.trim();
      if (!value || !uuidPattern.test(value)) {
        setIdError({ batch: batchId, message: !value ? 'Введите ID пакета' : 'ID пакета должен быть UUID, например 3f2504e0-4f89-41d4-9a0c-0305e82c3301' });
        return;
      }
      setIdError(null); action.reset();
      if (value === batchId) refresh();
      else router.push(`/import-report?batch=${encodeURIComponent(value)}`);
    }}>
      <label>ID пакета<input placeholder="Введите ID пакета" value={id} onChange={event => {
        setDraft({ batch: batchId, value: event.target.value }); setIdError(null);
      }} /></label>
      <button className="button primary">Открыть отчёт</button>
      <Feedback error={idError?.batch === batchId ? idError.message : undefined} />
    </form>
    {batchId && <section className="report-result" aria-label="Данные отчёта">
      <div className="section-header">
        <div>{batch && <><h2>{batch.filename}</h2>
          <p>{importTypes[batch.import_type]} · {quarterName(batch.selected_quarter)} · {displayDate(batch.snapshot_date)}</p>
        </>}</div>
        <button type="button" className="button secondary" onClick={refresh}><RefreshCw aria-hidden="true" />Обновить отчёт</button>
      </div>
      <Feedback {...report} />
      {batch && <>
        <Badge tone={reportStatus[batch.status][1]}>{reportStatus[batch.status][0]}</Badge>
        <Metrics items={[
          ['Строк в файле', batch.status === 'PROCESSING' ? '—' : batch.total_rows],
          ['Принято', batch.status === 'PROCESSING' ? '—' : batch.success_count],
          ['Ошибок', batch.status === 'PROCESSING' ? '—' : batch.error_count],
        ]} />
        {batch.status === 'PROCESSING' && <p className="feedback feedback-info" role="status">Файл обрабатывается. Статистика появится после завершения импорта.</p>}
        {batch.status === 'FAILED' && <p className="feedback feedback-bad" role="alert">Импорт завершился с ошибкой.</p>}
        {batch.status === 'ROLLED_BACK' && <p className="feedback feedback-good" role="status">Данные восстановлены до состояния перед загрузкой.</p>}
        {!showRollback && <Feedback notice={action.notice} />}
        {batch.status === 'COMPLETED' && <div className="report-actions">
          <button type="button" className="button secondary" aria-haspopup="dialog" disabled={action.busy || report.loading}
            onClick={() => { action.reset(); setRollbackId(batch.id); }}>Откатить пакет</button>
        </div>}
        <Modal open={showRollback} title="Откатить пакет?" description="Система проверит, что это последний применённый пакет."
          busy={action.busy} onClose={() => { setRollbackId(null); action.reset(); }}>
          <dl className="dialog-details"><dt>Файл</dt><dd>{batch.filename}</dd><dt>Квартал загрузки</dt><dd>{quarterName(batch.selected_quarter)}</dd></dl>
          <Feedback error={action.error} />
          <ConfirmActions label="Откатить пакет" onConfirm={rollback} onClose={() => { setRollbackId(null); action.reset(); }}
            busy={action.busy} danger />
        </Modal>
      </>}
    </section>}
    {userId && <ImportHistory key={`${userId}:${batchId}`} userId={userId} batch={batch} />}
  </>;
}

export function Imports({ reportOnly }: { reportOnly: boolean }) {
  return reportOnly ? <ImportReports /> : <UploadForm />;
}
