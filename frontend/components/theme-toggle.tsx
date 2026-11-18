'use client';

import { Moon, Sun } from 'lucide-react';
import { themeKey } from '@/lib/theme';

export function ThemeToggle() {
  function toggleTheme() {
    const root = document.documentElement;
    const current = root.dataset.theme ?? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    const next = current === 'dark' ? 'light' : 'dark';
    const applyTheme = () => {
      root.dataset.theme = next;
      try { localStorage.setItem(themeKey, next); } catch { /* Storage may be disabled. */ }
    };
    if (document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      document.startViewTransition(applyTheme);
    } else {
      applyTheme();
    }
  }

  return <button className="theme-toggle" type="button" onClick={toggleTheme}>
    <span className="theme-to-dark"><Moon size={16} aria-hidden="true" /><span className="sr-only">Тёмная тема</span></span>
    <span className="theme-to-light"><Sun size={16} aria-hidden="true" /><span className="sr-only">Светлая тема</span></span>
  </button>;
}
