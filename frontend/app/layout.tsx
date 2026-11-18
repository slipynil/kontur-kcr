import type { Metadata } from 'next';
import './globals.css';
import 'react-day-picker/style.css';
import './theme.css';
import Workspace from '@/components/workspace';
import { Suspense } from 'react';
import { Inter } from 'next/font/google';
import { initializeTheme } from '@/lib/theme';

const inter = Inter({ subsets: ['latin', 'cyrillic'], display: 'swap', variable: '--font-inter' });

export const metadata: Metadata = {
  title: 'Контур КЦР — контроль передачи документов',
  description: 'Реестр клиентов и контроль передачи оригиналов документов',
};

export default function RootLayout({ children }: LayoutProps<'/'>) {
  return (
    <html lang="ru" suppressHydrationWarning className={inter.variable}>
      <head><script dangerouslySetInnerHTML={{ __html: initializeTheme }} /></head>
      <body>
        <Suspense fallback={<main className="auth-loading" aria-label="Загрузка" />}><Workspace /></Suspense>
        {children}
      </body>
    </html>
  );
}
