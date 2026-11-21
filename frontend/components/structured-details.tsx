import type { ReactNode } from 'react';

const labels: Record<string, string> = {
  status: 'Статус', is_blocked: 'Блокировка', quarter: 'Исходный квартал',
  snapshot_date: 'Дата среза', batch_id: 'Пакет импорта', import_batch_id: 'Пакет импорта',
  inn: 'ИНН', kpp: 'КПП', organization_name: 'Название организации',
  manager_name: 'Менеджер', contacts: 'Контакты', phone: 'Телефон',
  email: 'Электронная почта', name: 'Имя', total_documents: 'Всего документов',
  unsubmitted_documents: 'Непереданные документы', tags: 'Признаки',
};

function renderValue(value: unknown): ReactNode {
  if (value == null || value === '') return 'Не указано';
  if (typeof value === 'boolean') return value ? 'Да' : 'Нет';
  if (Array.isArray(value)) return <ul>{value.map((item, index) => <li key={index}>{renderValue(item)}</li>)}</ul>;
  if (typeof value === 'object') return <StructuredDetails values={value as Record<string, unknown>} />;
  return String(value);
}

export function StructuredDetails({ values }: { values?: Record<string, unknown> | null }) {
  if (!values || Object.keys(values).length === 0) return <p>Сведения не указаны</p>;
  return <dl className="structured-details">{Object.entries(values).map(([field, value]) =>
    <div key={field}><dt>{labels[field] ?? field}</dt><dd>{renderValue(value)}</dd></div>
  )}</dl>;
}
