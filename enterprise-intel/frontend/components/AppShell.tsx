"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ReactNode, useEffect, useState } from "react";
import { api, logout, refreshSession } from "@/lib/api";

const LINKS = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/documents", label: "Documents" },
  { href: "/chat", label: "Chat" },
  { href: "/logs", label: "Logs" },
];

export default function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [email, setEmail] = useState<string | null>(null);

  // Client-side guard: the access token is in memory, so a reload restores it from the refresh cookie first.
  useEffect(() => {
    refreshSession().then(async (ok) => {
      if (!ok) return router.replace("/login");
      setEmail((await api<{ email: string }>("/auth/me")).email);
    });
  }, [router]);

  if (!email) return <main><p className="muted">Loading…</p></main>;

  return (
    <>
      <header className="topbar">
        <strong>Enterprise Intelligence</strong>
        <nav>
          {LINKS.map((l) => (
            <Link key={l.href} href={l.href} className={pathname.startsWith(l.href) ? "active" : ""}>
              {l.label}
            </Link>
          ))}
        </nav>
        <span className="spacer" />
        <span className="muted">{email}</span>
        <button className="secondary" onClick={async () => { await logout(); router.push("/login"); }}>Sign out</button>
      </header>
      {children}
    </>
  );
}
