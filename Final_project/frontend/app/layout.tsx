import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import PrivacyNotice from "@/components/PrivacyNotice";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "Research Assistant — Multi-Agent",
  description:
    "A Supervisor agent coordinates a tool-using Researcher (web, Wikipedia and your documents), a Writer, a Reviewer and a Fact-Checker to write a cited research report.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}>
      <body className="flex min-h-full flex-col">
        <header className="border-b border-zinc-200 dark:border-zinc-800">
          <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3">
            <div className="min-w-0">
              <h1 className="text-lg font-semibold">
                <Link href="/">Research Assistant</Link>
              </h1>
              <p className="text-xs text-zinc-600 dark:text-zinc-400">
                Supervisor · Researcher · Writer · Reviewer · Fact-Checker
              </p>
            </div>
            <nav aria-label="Main" className="flex gap-4 text-sm font-medium">
              <Link href="/" className="text-orange-800 underline dark:text-orange-300">
                Research
              </Link>
              <Link href="/library" className="text-orange-800 underline dark:text-orange-300">
                Library
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto flex w-full max-w-7xl flex-1 flex-col px-4 py-4">{children}</main>
        <footer className="mx-auto w-full max-w-7xl px-4 pb-6">
          <PrivacyNotice />
        </footer>
      </body>
    </html>
  );
}
