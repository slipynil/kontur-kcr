'use client';

import { CircleDot, X } from 'lucide-react';
import { Dialog, Tabs } from 'radix-ui';
import { useRef, type ReactNode } from 'react';

export function Brand() {
  return <span className="brand"><CircleDot size={18} aria-hidden="true" />Контур КЦР</span>;
}

export function PageHeader({ title, description }: { title: string; description?: ReactNode }) {
  return <header className="page-header">
    <div><h1>{title}</h1>{description && <p>{description}</p>}</div>
    <Brand />
  </header>;
}

export function Metrics({ items }: { items: Array<[string, ReactNode]> }) {
  return <div className="metrics">{items.map(([label, value]) =>
    <div className="stat-card" key={label}><small>{label}</small><b>{value ?? '—'}</b></div>,
  )}</div>;
}

export function Badge({ children, tone = 'neutral' }: {
  children: ReactNode;
  tone?: 'neutral' | 'good' | 'caution' | 'bad' | 'accent';
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function EmptyState({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return <div className="empty-state"><h2>{title}</h2>{description && <p>{description}</p>}{children}</div>;
}

export function Modal({ open, title, description, onClose, busy = false, wide = false, children }: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  busy?: boolean;
  wide?: boolean;
  children: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  return <Dialog.Root open={open} onOpenChange={next => { if (!next && !busy) onClose(); }}>
    <Dialog.Portal>
      <Dialog.Overlay className="modal-overlay" />
      <Dialog.Content className={`modal-card${wide ? ' modal-wide' : ''}`}
        {...(!description ? { 'aria-describedby': undefined } : {})}
        onOpenAutoFocus={event => {
          opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
          event.preventDefault(); heading.current?.focus();
        }}
        onCloseAutoFocus={event => {
          event.preventDefault();
          if (opener.current?.isConnected) opener.current.focus();
        }}>
        <div className="modal-header">
          <Dialog.Title ref={heading} tabIndex={-1}>{title}</Dialog.Title>
          {description && <Dialog.Description>{description}</Dialog.Description>}
          <Dialog.Close className="button icon-button modal-close" aria-label="Закрыть" disabled={busy}><X size={16} /></Dialog.Close>
        </div>
        {children}
      </Dialog.Content>
    </Dialog.Portal>
  </Dialog.Root>;
}

export function ConfirmActions({ onClose, onConfirm, label, busy, danger }: {
  onClose: () => void; onConfirm: () => void; label: string; busy: boolean; danger?: boolean;
}) {
  return <div className="modal-actions">
    <button className="button secondary" type="button" onClick={onClose} disabled={busy}>Отмена</button>
    <button className={`button ${danger ? 'danger' : 'primary'}`} type="button" onClick={onConfirm} disabled={busy}>{label}</button>
  </div>;
}

export { Tabs };
