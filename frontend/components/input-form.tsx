'use client';

import { useState, type FormEvent } from 'react';
import type { InputRequest } from '@/lib/contracts';
import { dynamics, durations, tags } from './api-ui';
import { DateField } from './date-field';

export type InputField = {
  name: string;
  label: string;
  kind: 'text' | 'number' | 'date' | 'boolean' | 'enum';
  required: boolean;
  options?: string[];
  integer?: boolean;
  min?: number;
  max?: number;
};

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

/** The input of the `request_input` tool: one request for missing data. */
export function readInputRequest(input: unknown): InputRequest | null {
  const { key, message, requested_schema } = record(input);
  return typeof key === 'string' && typeof message === 'string' && requested_schema && typeof requested_schema === 'object'
    ? { key, message, requested_schema: requested_schema as Record<string, unknown> } : null;
}

/** Fields of the forms: a flat JSON Schema of primitive fields, in the order of the schema. */
export function readInputFields(requests: InputRequest[]): InputField[] {
  const fields = new Map<string, InputField>();
  for (const { requested_schema } of requests) {
    const { properties, required } = record(requested_schema);
    const names = Array.isArray(required) ? required.map(String) : [];
    for (const [name, value] of Object.entries(record(properties))) {
      const definition = record(value);
      const type = Array.isArray(definition.type) ? definition.type[0] : definition.type;
      fields.set(name, {
        name,
        label: typeof definition.title === 'string' && definition.title ? definition.title : name,
        kind: Array.isArray(definition.enum) ? 'enum' : definition.format === 'date' ? 'date'
          : type === 'boolean' ? 'boolean' : type === 'number' || type === 'integer' ? 'number' : 'text',
        required: names.includes(name),
        options: Array.isArray(definition.enum) ? definition.enum.map(String) : undefined,
        integer: type === 'integer',
        min: typeof definition.minimum === 'number' ? definition.minimum : undefined,
        max: typeof definition.maximum === 'number' ? definition.maximum : undefined,
      });
    }
  }
  return [...fields.values()];
}

/** Values of the form in the types of the schema; empty fields are omitted and listed when required. */
export function readInputContent(fields: InputField[], values: Record<string, string>) {
  const content: Record<string, unknown> = {};
  const invalid: string[] = [];
  for (const field of fields) {
    const raw = values[field.name]?.trim() ?? '';
    if (!raw) {
      if (field.required) invalid.push(field.label);
      continue;
    }
    const number = field.kind === 'number' ? Number(raw) : NaN;
    const inRange = number >= (field.min ?? -Infinity) && number <= (field.max ?? Infinity);
    if (field.kind === 'number' ? !Number.isFinite(number) || (field.integer && !Number.isInteger(number)) || !inRange
      : field.kind === 'boolean' ? raw !== 'true' && raw !== 'false'
        : field.kind === 'enum' ? !field.options?.includes(raw)
          : field.kind === 'date' && !/^\d{4}-\d{2}-\d{2}$/.test(raw)) invalid.push(field.label);
    else if (field.kind === 'number') content[field.name] = number;
    else if (field.kind === 'boolean') content[field.name] = raw === 'true';
    else content[field.name] = raw;
  }
  return { content, invalid };
}

/** The answer in words, to show it and to repeat a request with the answer. */
export function describeInputAnswer(content: Record<string, unknown>, fields: InputField[]): string[] {
  return Object.entries(content).map(([name, value]) =>
    `${fields.find(field => field.name === name)?.label ?? name}: ${value === true ? 'Да' : value === false ? 'Нет' : String(value)}`);
}

function optionLabel(option: string) {
  return dynamics[option as keyof typeof dynamics] ?? durations[option as keyof typeof durations] ?? tags[option as keyof typeof tags] ?? option;
}

export function InputForm({ requests, submitLabel = 'Отправить', busy = false, onSubmit, onCancel }: {
  requests: InputRequest[];
  submitLabel?: string;
  busy?: boolean;
  onSubmit: (content: Record<string, unknown>) => void;
  onCancel: () => void;
}) {
  const fields = readInputFields(requests);
  const [values, setValues] = useState<Record<string, string>>({});
  const [invalid, setInvalid] = useState<string[]>([]);

  function change(name: string, value: string) {
    setValues(current => ({ ...current, [name]: value }));
    setInvalid([]);
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const result = readInputContent(fields, values);
    if (result.invalid.length) { setInvalid(result.invalid); return; }
    onSubmit(result.content);
  }

  return <form className="input-form" noValidate onSubmit={submit} aria-label="Запрос на ввод">
    {requests.map((request, index) => <p className="input-request" key={index}>{request.message}</p>)}
    <div className="input-fields">
      {fields.map(field => {
        const options = field.kind === 'boolean' ? ['true', 'false'] : field.options ?? [];
        const flagged = invalid.includes(field.name) ? 'true' : undefined;
        return field.kind === 'date' ? <DateField key={field.name} label={field.label} required={field.required}
          value={values[field.name] ?? ''} onChange={value => change(field.name, value)} />
          : field.kind === 'boolean' || field.kind === 'enum' ? <label key={field.name}>{field.label}
            <select value={values[field.name] ?? ''} aria-invalid={flagged} required={field.required} onChange={event => change(field.name, event.target.value)}>
              <option value="">{field.required ? 'Выберите' : 'Не указано'}</option>
              {options.map(option => <option key={option} value={option}>{field.kind === 'boolean' ? (option === 'true' ? 'Да' : 'Нет') : optionLabel(option)}</option>)}
            </select></label>
          : <label key={field.name}>{field.label}
            <input type={field.kind === 'number' ? 'number' : 'text'} min={field.min} max={field.max} step={field.integer ? 1 : 'any'}
              inputMode={field.kind === 'number' ? 'decimal' : undefined} required={field.required} aria-invalid={flagged} maxLength={2000}
              value={values[field.name] ?? ''} onChange={event => change(field.name, event.target.value)} />
          </label>;
      })}
    </div>
    {invalid.length > 0 && <p className="feedback feedback-bad" role="alert">Заполните корректно: {invalid.join(', ')}</p>}
    <div className="modal-actions">
      <button className="button primary" type="submit" disabled={busy}>{submitLabel}</button>
      <button className="button secondary" type="button" disabled={busy} onClick={onCancel}>Отмена</button>
    </div>
  </form>;
}
