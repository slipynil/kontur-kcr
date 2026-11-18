'use client';

import { useId, useRef, useState } from 'react';
import { Popover } from 'radix-ui';
import { DayPicker } from 'react-day-picker';
import { ru } from 'react-day-picker/locale';

export function DateField({ label, value, onChange, required, withTime }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
  withTime?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const control = useRef<HTMLDivElement>(null);
  const keyboardOpen = useRef(false);
  const selected = value ? new Date(value.slice(0, 10) + 'T00:00:00') : undefined;
  const time = value.slice(11, 16);

  function closePicker() {
    input.current?.focus();
    setOpen(false);
  }

  return <div className="date-field">
    <label htmlFor={id}>{label}</label>
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
      <div className="date-field-control" ref={control}>
        <input ref={input} id={id} type={withTime ? 'datetime-local' : 'date'} value={value} required={required}
          aria-haspopup="dialog"
          onClick={() => { keyboardOpen.current = false; setOpen(true); }}
          onKeyDown={event => {
            if (/^[0-9]$/.test(event.key)) setOpen(false);
            if (event.altKey && event.key === 'ArrowDown') {
              event.preventDefault();
              keyboardOpen.current = true;
              setOpen(true);
            }
          }}
          onChange={event => { onChange(event.target.value); setOpen(false); }} />
      </div>
      </Popover.Anchor>
        <Popover.Portal>
          <Popover.Content align="start" sideOffset={6} collisionPadding={8} className="date-picker" aria-label={`Календарь: ${label}`}
            onOpenAutoFocus={event => { if (!keyboardOpen.current) event.preventDefault(); }}
            onCloseAutoFocus={event => event.preventDefault()}
            onEscapeKeyDown={() => input.current?.focus()}
            onInteractOutside={event => { if (control.current?.contains(event.target as Node)) event.preventDefault(); }}>
            <DayPicker mode="single" locale={ru} captionLayout="dropdown" navLayout="after"
              startMonth={new Date(1900, 0)} endMonth={new Date(2100, 11)}
              animate selected={selected} defaultMonth={selected} onSelect={day => {
              if (!day) return;
              const date = [day.getFullYear(), day.getMonth() + 1, day.getDate()]
                .map((part, index) => String(part).padStart(index === 0 ? 4 : 2, '0')).join('-');
              onChange(withTime ? date + 'T' + (time || '00:00') : date);
              if (!withTime) closePicker();
            }} />
            {withTime && <div className="date-picker-time">
              <label htmlFor={`${id}-time`}>Время комментария</label>
              <input id={`${id}-time`} type="time" value={time} disabled={!selected}
                onChange={event => {
                  if (event.target.value) onChange(value.slice(0, 10) + 'T' + event.target.value);
                }} />
              <button className="button primary" type="button" onClick={closePicker}>Готово</button>
            </div>}
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
  </div>;
}
