'use client';

import { Filter, RefreshCw, Search, Upload, X } from 'lucide-react';
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { queryString, request } from '@/lib/api';
import type { ClientListResponse, PoolPublicationRequest, User } from '@/lib/contracts';
import { currentQuarter, dynamics, durations, Feedback, Pager, QuarterInput, quarterName, quarterOffset, quarterPattern, stages, tags, useAction, useResource } from './api-ui';
import { Badge, ConfirmActions, EmptyState, Metrics, Modal, PageHeader } from './presentation';

const PAGE_SIZE = 20;
const REGISTRY_PAGE_SIZE = 100;
const PUBLICATION_BATCH_SIZE = 1000;
const bounds = [
  ['min_unsubmitted_documents', 'Не передано от'], ['max_unsubmitted_documents', 'Не передано до'],
  ['min_unsubmitted_percent', 'Процент от'], ['max_unsubmitted_percent', 'Процент до'],
];
const dynamicsTone = { ALL_SUBMITTED: 'good', REDUCING: 'good', NO_CHANGE: 'neutral', GROWING: 'bad' } as const;

export function Clients({ user, side, top }: { user: User; side?: ReactNode; top?: ReactNode }) {
  const router = useRouter();
  const admin = user.role === 'ADMIN';
  const [filtersOpen, setFiltersOpen] = useState(() => !globalThis.matchMedia?.('(max-width: 850px)').matches);
  const filterHeading = useRef<HTMLHeadingElement>(null);
  const filterToggle = useRef<HTMLButtonElement>(null);
  const moveFilterFocus = useRef(false);
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState<string[]>([]);
  const [publishQuarter, setPublishQuarter] = useState('');
  const [publicationOpen, setPublicationOpen] = useState(false);
  const [publishAllCodes, setPublishAllCodes] = useState<string[] | null>(null);
  const action = useAction();
  const search = (draft.search ?? '').trim();
  const appliedSearch = filters.search ?? '';
  const result = useResource<ClientListResponse>('/clients' + queryString({ ...filters, page, page_size: PAGE_SIZE }), revision);
  const rows = result.data?.items ?? [];
  const shownSelection = rows.filter(row => selection.includes(row.kcr_code)).map(row => row.kcr_code);
  const displayQuarter = filters.quarter || currentQuarter();
  const filterCount = Object.entries(filters).filter(([name, value]) => name !== 'search' && value).length;

  useEffect(() => {
    if (search === appliedSearch) return;
    const timer = setTimeout(() => {
      setFilters(current => ({ ...current, search }));
      setPage(1); setSelection([]);
    }, 300);
    return () => clearTimeout(timer);
  }, [search, appliedSearch]);

  useEffect(() => {
    if (!moveFilterFocus.current) return;
    moveFilterFocus.current = false;
    if (filtersOpen) filterHeading.current?.focus();
    else filterToggle.current?.focus();
  }, [filtersOpen]);

  function toggleFilters() {
    moveFilterFocus.current = true;
    setFiltersOpen(open => !open);
  }

  function change(name: string, value: string) { setDraft(current => ({ ...current, [name]: value })); }
  function clearFilters() { setDraft({}); setFilters({}); setPage(1); setSelection([]); }
  function showClient(code: string) { router.push(`/client/${encodeURIComponent(code)}`); }
  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters({ ...draft, search }); setPage(1); setSelection([]);
  }
  function checkedQuarter() {
    if (!quarterPattern.test(publishQuarter)) throw new Error('Квартал обзвона должен быть в формате 2026_Q4');
    return publishQuarter;
  }
  async function publish(codes: string[], workQuarter: string) {
    await request<void>('/pool/publication' + queryString({ work_quarter: workQuarter }), {
      method: 'PUT', body: JSON.stringify({ codes } satisfies PoolPublicationRequest),
    });
  }
  function published(count: number) {
    setSelection([]); setPublicationOpen(false); setPublishAllCodes(null);
    setRevision(current => current + 1);
    action.setNotice(`Опубликовано клиентов: ${count}`);
  }
  function prepareAll() {
    void action.run(async () => {
      checkedQuarter();
      const codes = new Set<string>();
      const loadPage = (page: number) => request<ClientListResponse>('/clients' + queryString({ page, page_size: REGISTRY_PAGE_SIZE }));
      const first = await loadPage(1);
      const pageCount = Math.ceil(first.total / first.page_size);
      first.items.forEach(client => codes.add(client.kcr_code));
      for (let nextPage = 2; nextPage <= pageCount; nextPage++) {
        const result = await loadPage(nextPage);
        if (result.total !== first.total) throw new Error('Реестр изменился. Повторите публикацию.');
        result.items.forEach(client => codes.add(client.kcr_code));
      }
      if (codes.size !== first.total) throw new Error('Реестр изменился. Повторите публикацию.');
      if (!codes.size) throw new Error('В реестре нет клиентов');
      setPublishAllCodes([...codes]);
    });
  }
  function confirmAll() {
    if (!publishAllCodes) return;
    const codes = publishAllCodes;
    void action.run(async () => {
      const quarter = checkedQuarter();
      let count = 0;
      try {
        while (count < codes.length) {
          const batch = codes.slice(count, count + PUBLICATION_BATCH_SIZE);
          await publish(batch, quarter);
          count += batch.length;
        }
      } catch (error) {
        if (count) setRevision(current => current + 1);
        const reason = error instanceof Error ? error.message : 'Ошибка запроса';
        throw new Error(`Подтверждена публикация ${count} из ${codes.length} клиентов. ${reason}`);
      }
      published(count);
    });
  }
  function closePublication() { setPublicationOpen(false); setPublishAllCodes(null); action.reset(); }

  return <div className={`registry-layout${filtersOpen || side ? '' : ' without-rail'}`}>
    {filtersOpen ? <form className="side-rail filter-rail" id="registry-filters" onSubmit={applyFilters} aria-label="Фильтры реестра">
      <div className="rail-heading"><h2 ref={filterHeading} tabIndex={-1} className="filter-heading"><Filter size={17} aria-hidden="true" />Фильтры</h2>
        <button type="button" className="button icon-button" aria-label="Скрыть фильтры" onClick={toggleFilters}><X aria-hidden="true" /></button>
      </div>
      <div data-filter-body>
        <label>Квартал сравнения<QuarterInput value={draft.quarter || ''} onChange={value => change('quarter', value)} /></label>
        <label>Динамика<select value={draft.dynamics || ''} onChange={event => change('dynamics', event.target.value)}>
          <option value="">Любая</option>{Object.entries(dynamics).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>Длительность<select value={draft.duration || ''} onChange={event => change('duration', event.target.value)}>
          <option value="">Любая</option>{Object.entries(durations).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <label>Признак<select value={draft.tags || ''} onChange={event => change('tags', event.target.value)}>
          <option value="">Все признаки</option>{Object.entries(tags).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select></label>
        <fieldset><legend>Остаток за квартал</legend>
          <div className="stack">
            {[false, true].map(percent => <div key={String(percent)}>
              <p className="help-text">{percent ? 'Не передано, %' : 'Не передано документов'}</p>
              <div className="range-pair">{bounds.slice(percent ? 2 : 0, percent ? 4 : 2).map(([key, label], index) =>
                <input key={key} type="number" min={0} max={percent ? 100 : 2147483647} step={percent ? 'any' : 1}
                  aria-label={label} placeholder={index ? 'До' : 'От'} value={draft[key] ?? ''} onChange={event => change(key, event.target.value)} />,
              )}</div>
            </div>)}
          </div>
        </fieldset>
      </div>
      <div className="filter-actions">
        <button type="submit" className="button primary">Применить</button>
        <button type="button" className="button text-link" onClick={clearFilters}>Сбросить</button>
      </div>
    </form> : side}
    <main className="workspace-body">
      <PageHeader title="Клиенты" description="Реестр клиентов и непереданные документы" />
      {filtersOpen && top}
      <Metrics items={[
        ['Всего клиентов по запросу', result.data?.total],
        ['Показано на странице', result.data?.items.length],
        ['Квартал данных', quarterName(displayQuarter)],
        ['Предыдущий квартал', quarterName(quarterOffset(displayQuarter, -1))],
      ]} />
      <div className="section-header"><div><h2>Реестр клиентов</h2><p>Откройте строку, чтобы посмотреть карточку клиента.</p></div></div>
      <div className="registry-toolbar">
        <label className="registry-search"><span className="sr-only">Код КЦР или ИНН</span>
          <span className="input-icon search-input"><Search size={16} aria-hidden="true" />
            <input form={filtersOpen ? 'registry-filters' : undefined} placeholder="Поиск по коду КЦР или ИНН" maxLength={256}
              value={draft.search || ''} onChange={event => change('search', event.target.value)} />
          </span>
        </label>
        <div className="inline-actions">
          <button type="button" className="button text-link" onClick={() => setRevision(current => current + 1)}><RefreshCw aria-hidden="true" />Обновить</button>
          <button type="button" className="button secondary" ref={filterToggle} aria-expanded={filtersOpen} aria-controls={filtersOpen ? 'registry-filters' : undefined}
            onClick={toggleFilters}><Filter aria-hidden="true" />Фильтры{filterCount ? ` · ${filterCount}` : ''}</button>
          {admin && <button type="button" className="button primary" aria-haspopup="dialog" disabled={!result.data || result.loading}
            onClick={() => { action.reset(); setPublicationOpen(true); }}><Upload aria-hidden="true" />Публикация на обзвон</button>}
        </div>
      </div>
      <Feedback {...result} />
      {!publicationOpen && <Feedback {...action} />}
      {shownSelection.length > 0 && <p className="help-text selection-label" role="status">Выбрано клиентов: {shownSelection.length}</p>}
      <div className="table-wrap" tabIndex={0} role="region" aria-label="Реестр клиентов">
        <table className="registry-table"><thead><tr>
          {admin && <th scope="col"><input type="checkbox" aria-label="Выбрать всех на странице"
            checked={rows.length > 0 && shownSelection.length === rows.length} disabled={!rows.length}
            onChange={event => setSelection(event.target.checked ? rows.map(row => row.kcr_code) : [])} /></th>}
          {['Код КЦР', 'Клиент / ИНН', 'За квартал', 'Всего', 'Динамика', 'Этап', 'Ответственный', 'Признаки'].map(label => <th scope="col" key={label}>{label}</th>)}
        </tr></thead><tbody>{rows.map(client => <tr key={client.kcr_code} className="client-row" onClick={event => {
          if (!(event.target as HTMLElement).closest('button, input, label, a') && !window.getSelection()?.toString()) showClient(client.kcr_code);
        }}>
          {admin && <td><input type="checkbox" aria-label={`Выбрать ${client.kcr_code}`} checked={selection.includes(client.kcr_code)}
            onChange={event => {
              const checked = event.target.checked;
              setSelection(current => checked ? [...current, client.kcr_code] : current.filter(code => code !== client.kcr_code));
            }} /></td>}
          <td><button type="button" className="text-link" onClick={() => showClient(client.kcr_code)}>{client.kcr_code}</button></td>
          <td><div className="client-identity"><span className="monogram" aria-hidden="true">{client.organization_name?.trim().charAt(0).toUpperCase() || 'К'}</span>
            <div><strong>{client.organization_name ?? 'Нет названия'}</strong><small>{client.inn ? `ИНН ${client.inn}` : 'ИНН не указан'}</small></div>
          </div></td>
          <td>{client.quarter_summary?.unsubmitted_documents ?? '—'} / {client.quarter_summary?.total_documents ?? '—'}</td>
          <td>{client.outstanding_documents ?? '—'}</td>
          <td><Badge tone={client.dynamics ? dynamicsTone[client.dynamics] : 'neutral'}>{client.dynamics ? dynamics[client.dynamics] : 'Не определена'}</Badge></td>
          <td>{client.current_stage ? stages[client.current_stage] : '—'}</td>
          <td className="mono-data">{client.assigned_operator_id ?
            <span title={client.assigned_operator_id} aria-label={`ID ответственного: ${client.assigned_operator_id}`}>ID {client.assigned_operator_id.slice(0, 8)}</span> : '—'}</td>
          <td>
            {client.needs_review && <Badge tone="caution">Требует проверки</Badge>}
            {client.tags.length > 0 && <div>{client.tags.map(tag => tags[tag as keyof typeof tags] ?? tag).join(', ')}</div>}
            {!client.needs_review && client.tags.length === 0 && '—'}
          </td>
        </tr>)}</tbody></table>
      </div>
      {!result.loading && result.data && !rows.length && <EmptyState title="Клиенты не найдены" description="Измените параметры поиска или сбросьте фильтры.">
        <button type="button" className="button secondary" onClick={clearFilters}>Сбросить фильтры</button>
      </EmptyState>}
      {result.data && <Pager page={page} total={result.data.total} pageSize={result.data.page_size} onChange={next => { setPage(next); setSelection([]); }} />}
    </main>
    {admin && <Modal open={publicationOpen} title={publishAllCodes ? 'Опубликовать весь реестр?' : 'Публикация на обзвон'}
      description={publishAllCodes ? 'Клиенты станут доступны операторам в выбранном квартале.' : 'Передача клиентов в операторский пул.'}
      onClose={closePublication} busy={action.busy}>
      {publishAllCodes ? <>
        <dl className="dialog-details"><dt>Квартал обзвона</dt><dd>{quarterName(publishQuarter)}</dd><dt>Всего клиентов</dt><dd>{publishAllCodes.length}</dd></dl>
        <Feedback error={action.error} />
        <ConfirmActions label="Опубликовать всех" busy={action.busy} onConfirm={confirmAll} onClose={() => { setPublishAllCodes(null); action.reset(); }} />
      </> : <form className="stack" noValidate onSubmit={event => {
        event.preventDefault();
        void action.run(async () => {
          if (!shownSelection.length) throw new Error('Выберите хотя бы одного клиента');
          await publish(shownSelection, checkedQuarter());
          published(shownSelection.length);
        });
      }}>
        <label>Квартал обзвона<QuarterInput required ariaLabel="Квартал обзвона" value={publishQuarter} onChange={setPublishQuarter} /></label>
        <p>Выбрано клиентов: {shownSelection.length}</p>
        <Feedback error={action.error} />
        <div className="stack">
          <button className="button primary" disabled={action.busy}>Опубликовать выбранных</button>
          <button type="button" className="button secondary" disabled={action.busy} onClick={prepareAll}>Опубликовать весь реестр</button>
        </div>
      </form>}
    </Modal>}
  </div>;
}
