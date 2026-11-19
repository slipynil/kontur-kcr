'use client';

import { CalendarDays } from 'lucide-react';
import { createElement, useId, useRef, useState, type ComponentProps } from 'react';
import { Popover } from 'radix-ui';
import { DayPicker } from 'react-day-picker';
import { ru } from 'react-day-picker/locale';

function CalendarSelect({ children, ...props }: ComponentProps<'select'>) {
  return <select {...props}>
    <button type="button">{createElement('selectedcontent')}</button>
    {children}
  </select>;
}

export function DateField({ label, value, onChange, required }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  required?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const control = useRef<HTMLDivElement>(null);
  const keyboardOpen = useRef(false);
  const selected = value ? new Date(value + 'T00:00:00') : undefined;

  function closePicker() {
    input.current?.focus();
    setOpen(false);
  }

  return <div className="date-field">
    <label htmlFor={id}>{label}</label>
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Anchor asChild>
      <div className="date-field-control" ref={control}>
        <input ref={input} id={id} type="date" value={value} required={required}
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
        <CalendarDays size={16} aria-hidden="true" />
      </div>
      </Popover.Anchor>
        <Popover.Portal>
          <Popover.Content align="start" sideOffset={6} collisionPadding={8} className="date-picker" aria-label={`Календарь: ${label}`}
            onOpenAutoFocus={event => { if (!keyboardOpen.current) event.preventDefault(); }}
            onCloseAutoFocus={event => event.preventDefault()}
            onEscapeKeyDown={() => input.current?.focus()}
            onInteractOutside={event => { if (control.current?.contains(event.target as Node)) event.preventDefault(); }}>
            <DayPicker mode="single" locale={ru} captionLayout="dropdown" navLayout="around"
              components={{ Select: CalendarSelect }}
              startMonth={new Date(1900, 0)} endMonth={new Date(2100, 11)}
              selected={selected} defaultMonth={selected} onSelect={day => {
              if (!day) return;
              const date = [day.getFullYear(), day.getMonth() + 1, day.getDate()]
                .map((part, index) => String(part).padStart(index === 0 ? 4 : 2, '0')).join('-');
              onChange(date);
              closePicker();
            }} />
          </Popover.Content>
        </Popover.Portal>
      </Popover.Root>
  </div>;
}
