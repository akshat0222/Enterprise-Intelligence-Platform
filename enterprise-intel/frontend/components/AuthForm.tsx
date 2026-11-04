"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { authenticate } from "@/lib/api";

export default function AuthForm({ mode }: { mode: "login" | "register" }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await authenticate(mode, email, password);
      router.push("/documents");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main>
      <form className="card auth" onSubmit={submit}>
        <h1 style={{ marginBottom: 16 }}>{mode === "login" ? "Sign in" : "Create account"}</h1>
        <label htmlFor="email">Email</label>
        <input id="email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <label htmlFor="password">Password {mode === "register" && "(min 8 characters)"}</label>
        <input id="password" type="password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="error">{error}</p>}
        <button disabled={busy} style={{ width: "100%" }}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Register"}
        </button>
        <p className="muted" style={{ textAlign: "center" }}>
          {mode === "login" ? (
            <>No account? <Link href="/register">Register</Link></>
          ) : (
            <>Have an account? <Link href="/login">Sign in</Link></>
          )}
        </p>
      </form>
    </main>
  );
}
