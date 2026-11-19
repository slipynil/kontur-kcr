import type { ImportBatch } from './contracts';

type HistoryEntry = Pick<ImportBatch, 'id' | 'filename'> & (
  | { import_type?: 'REGISTRY' }
  | Pick<Extract<ImportBatch, { import_type: 'SNAPSHOT' }>, 'import_type' | 'selected_quarter' | 'snapshot_date'>
);

export function readImportHistory(userId: string): HistoryEntry[] {
  try {
    const entries: unknown = JSON.parse(localStorage.getItem(`kontur-import-history:${userId}`) ?? '[]');
    if (!Array.isArray(entries)) return [];
    return entries.flatMap<HistoryEntry>(entry => {
      if (!entry || typeof entry.id !== 'string' || typeof entry.filename !== 'string') return [];
      const { id, filename, import_type, selected_quarter, snapshot_date } = entry;
      if (import_type === 'SNAPSHOT' && typeof selected_quarter === 'string' && typeof snapshot_date === 'string') {
        return [{ id, filename, import_type, selected_quarter, snapshot_date }];
      }
      return [{ id, filename, import_type: import_type === 'REGISTRY' ? import_type : undefined }];
    });
  } catch {
    return [];
  }
}

export function includeImport(entries: HistoryEntry[], batch: ImportBatch): HistoryEntry[] {
  const { id, filename, import_type } = batch;
  const entry: HistoryEntry = import_type === 'SNAPSHOT'
    ? { id, filename, import_type, selected_quarter: batch.selected_quarter, snapshot_date: batch.snapshot_date }
    : { id, filename, import_type };
  return [entry, ...entries.filter(saved => saved.id !== id)].slice(0, 50);
}

export function rememberImport(userId: string, batch: ImportBatch) {
  try {
    const entries = includeImport(readImportHistory(userId), batch);
    localStorage.setItem(`kontur-import-history:${userId}`, JSON.stringify(entries));
  } catch {
    // History is optional when browser storage is unavailable.
  }
}
