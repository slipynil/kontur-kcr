import type { Metadata } from "next";
import "./globals.css";
import "react-day-picker/style.css";
import "./theme.css";
import Workspace from "@/components/workspace";
import { Suspense } from "react";
import { Geist } from "next/font/google";
import { cn } from "@/lib/utils";
import { initializeTheme } from "@/lib/theme";

const geist = Geist({subsets:['latin'],variable:'--font-sans'});

export const metadata: Metadata = {
  title: "Контур КЦР — контроль передачи документов",
  description: "Реестр клиентов и контроль передачи оригиналов документов",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ru"
      suppressHydrationWarning
      className={cn("h-full antialiased", "font-sans", geist.variable)}
    >
      <head><script dangerouslySetInnerHTML={{ __html: initializeTheme }} /></head>
      <body className="min-h-full flex flex-col"><Suspense fallback={<main className="auth-loading" aria-label="Загрузка"/>}><Workspace/></Suspense>{children}</body>
    </html>
  );
}
