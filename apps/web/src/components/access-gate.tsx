"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { AUTH_REQUIRED_EVENT, IS_MOCK, api, setAdminToken } from "@/lib/api";

/** Asks for the access code when the backend answers 401 (it was started with ADMIN_TOKEN). */
export function AccessGate() {
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (IS_MOCK) return;
    const show = () => setOpen(true);
    window.addEventListener(AUTH_REQUIRED_EVENT, show);
    api.listJobs().catch(() => {}); // a 401 here opens the gate before any view has data
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, show);
  }, []);

  if (!open) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAdminToken(code.trim());
    try {
      await api.listJobs();
      window.location.reload();
    } catch {
      setError("Wrong access code.");
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-xl bg-card p-6 shadow-lg">
        <div className="space-y-1">
          <h2 className="text-lg font-bold">Access code</h2>
          <p className="text-sm text-muted-foreground">This Admin can publish to Instagram. Enter the code you were given.</p>
        </div>
        <input
          type="password"
          autoFocus
          value={code}
          onChange={(e) => setCode(e.target.value)}
          className="w-full rounded-lg border px-3 py-2 text-sm"
          data-testid="access-code"
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button type="submit" className="w-full" disabled={!code.trim()}>
          Continue
        </Button>
      </form>
    </div>
  );
}
