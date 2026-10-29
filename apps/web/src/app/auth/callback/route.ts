// Email links (sign-up confirmation, password reset) land here with a one-time `code` (PKCE). Exchanging it
// sets the session cookies; the code verifier is in this browser's cookies, so the link must be opened in the
// browser that asked for it.

import { createServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { NextResponse, type NextRequest } from "next/server";

import { AUTH_CONFIGURED, SUPABASE_KEY, SUPABASE_URL, safeNext } from "@/lib/supabase";

export async function GET(request: NextRequest) {
  const url = request.nextUrl;
  const code = url.searchParams.get("code");
  const next = safeNext(url.searchParams.get("next"));
  if (code && AUTH_CONFIGURED) {
    const jar = await cookies();
    const supabase = createServerClient(SUPABASE_URL, SUPABASE_KEY, {
      cookies: {
        getAll: () => jar.getAll(),
        setAll: (list) => list.forEach(({ name, value, options }) => jar.set(name, value, options)),
      },
    });
    const { error } = await supabase.auth.exchangeCodeForSession(code);
    if (!error) return NextResponse.redirect(new URL(next, url.origin));
  }
  return NextResponse.redirect(new URL("/sign-in?link_error=1", url.origin));
}
