import type { Metadata } from "next";
import "./globals.css";
import Workspace from "@/components/workspace";
import { Suspense } from "react";

export const metadata: Metadata = {
  title: "Контур КЦР — контроль передачи документов",
  description: "Реестр клиентов и контроль передачи оригиналов документов",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ru"
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col"><Suspense fallback={<main className="auth-loading" aria-label="Загрузка"/>}><Workspace/></Suspense>{children}</body>
    </html>
  );
}
