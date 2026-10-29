// Supabase Auth for the Admin (ADR 0001). The session lives in cookies (@supabase/ssr) so src/proxy.ts can
// refresh it and send signed-out visitors to /sign-in before a page renders. The API never trusts the
// Admin: it verifies the access token we send as `Authorization: Bearer` on every call (backend/app/auth.py).
//
// Sign-in is required whenever the Admin talks to the real API (NEXT_PUBLIC_API_URL set). The in-memory
// mock API has nothing to protect and stays sign-in free.

import { createBrowserClient } from "@supabase/ssr";
import type { SupabaseClient } from "@supabase/supabase-js";

export const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
// The publishable key (`sb_publishable_…`, or the legacy anon key) is public by design; never a secret key.
export const SUPABASE_KEY = process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY ?? "";
export const AUTH_REQUIRED = !!process.env.NEXT_PUBLIC_API_URL;
export const AUTH_CONFIGURED = !!(SUPABASE_URL && SUPABASE_KEY);

/** Pages a signed-out person may open. */
export const AUTH_PATHS = ["/sign-in", "/sign-up", "/forgot-password", "/reset-password", "/auth/callback"];
export const isAuthPath = (path: string) => AUTH_PATHS.some((p) => path === p || path.startsWith(`${p}/`));

let client: SupabaseClient | undefined;
export function supabase(): SupabaseClient {
  if (!AUTH_CONFIGURED) throw new Error("Sign-in is not configured: set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY.");
  client ??= createBrowserClient(SUPABASE_URL, SUPABASE_KEY);
  return client;
}

/** The current access token, refreshed by supabase-js when it is about to expire; "" when signed out. */
export async function accessToken(): Promise<string> {
  if (!AUTH_REQUIRED || !AUTH_CONFIGURED) return "";
  const { data } = await supabase().auth.getSession();
  return data.session?.access_token ?? "";
}

/** Only same-site paths, so a crafted `?next=` link can't bounce someone to another site after sign-in. */
export function safeNext(next: string | null | undefined, fallback = "/"): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : fallback;
}

/** A full page load rather than router.push: after the session changes, the proxy and the sidebar must start
 * over with the new cookies. */
export function reloadTo(path: string) {
  window.location.assign(path);
}

export async function signOut() {
  await supabase().auth.signOut().catch(() => {});
  reloadTo("/sign-in");
}
