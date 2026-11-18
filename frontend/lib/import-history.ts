import type { ImportBatch } from './contracts';

type HistoryEntry = Pick<ImportBatch, 'id' | 'filename' | 'selected_quarter' | 'snapshot_date'>;

export function readImportHistory(userId: string): HistoryEntry[] {
  try {
    const entries: unknown = JSON.parse(localStorage.getItem(`kontur-import-history:${userId}`) ?? '[]');
    return Array.isArray(entries) ? entries.filter(entry =>
      entry && typeof entry.id === 'string' && typeof entry.filename === 'string'
      && typeof entry.selected_quarter === 'string' && typeof entry.snapshot_date === 'string',
    ) : [];
  } catch {
    return [];
  }
}

export function includeImport(entries: HistoryEntry[], batch: ImportBatch): HistoryEntry[] {
  const { id, filename, selected_quarter, snapshot_date } = batch;
  return [{ id, filename, selected_quarter, snapshot_date }, ...entries.filter(entry => entry.id !== id)].slice(0, 50);
}

export function rememberImport(userId: string, batch: ImportBatch) {
  try {
    const entries = includeImport(readImportHistory(userId), batch);
    localStorage.setItem(`kontur-import-history:${userId}`, JSON.stringify(entries));
  } catch {
    // History is optional when browser storage is unavailable.
  }
}
