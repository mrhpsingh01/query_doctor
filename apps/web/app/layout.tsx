import type { Metadata } from "next";
import { JetBrains_Mono, Schibsted_Grotesk } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const grotesk = Schibsted_Grotesk({ variable: "--font-grotesk", subsets: ["latin"] });
const code = JetBrains_Mono({ variable: "--font-code", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "QueryDoctor",
  description: "Find out why a PostgreSQL query is slow, and which fix is backed by measurements.",
};

const NAV = [
  { href: "/analyses/new", label: "New analysis" },
  { href: "/projects", label: "History" },
  { href: "/settings", label: "Settings" },
];

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${grotesk.variable} ${code.variable} h-full`}>
      <body className="min-h-full flex flex-col">
        <header className="border-b border-line bg-surface">
          <div className="mx-auto flex w-full max-w-[1320px] items-center gap-8 px-4 py-3 sm:px-6">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">
                <rect x="1" y="2" width="8" height="5" rx="1" fill="var(--heat-3)" />
                <rect x="13" y="2" width="8" height="5" rx="1" fill="var(--heat-0)" />
                <rect x="7" y="15" width="8" height="5" rx="1" fill="var(--heat-2)" />
                <path d="M5 7v4h12V7M11 11v4" stroke="var(--ink-2)" strokeWidth="1.5" fill="none" />
              </svg>
              QueryDoctor
            </Link>
            <nav aria-label="Main" className="flex gap-5 text-sm text-ink-2">
              {NAV.map((item) => (
                <Link key={item.href} href={item.href} className="hover:text-ink">
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1320px] flex-1 px-4 py-6 sm:px-6">{children}</main>
      </body>
    </html>
  );
}
