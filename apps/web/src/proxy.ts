// Runs before every page: refreshes the Supabase session cookies and sends signed-out visitors to /sign-in
// (and signed-in ones away from it). An optimistic check for a smooth Admin; the API verifies every call itself.

import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

import { AUTH_CONFIGURED, AUTH_REQUIRED, SUPABASE_KEY, SUPABASE_URL, isAuthPath } from "@/lib/supabase";

const SIGNED_OUT_ONLY = ["/sign-in", "/sign-up", "/forgot-password"];

export async function proxy(request: NextRequest) {
  if (!AUTH_REQUIRED) return NextResponse.next({ request }); // mock API: nothing to protect
  const path = request.nextUrl.pathname;
  if (!AUTH_CONFIGURED) {
    // /sign-in explains which variables are missing.
    return path === "/sign-in" ? NextResponse.next({ request }) : NextResponse.redirect(new URL("/sign-in", request.url));
  }

  let response = NextResponse.next({ request });
  const supabase = createServerClient(SUPABASE_URL, SUPABASE_KEY, {
    cookies: {
      getAll: () => request.cookies.getAll(),
      setAll(cookies, headers) {
        cookies.forEach(({ name, value }) => request.cookies.set(name, value));
        response = NextResponse.next({ request });
        cookies.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
        Object.entries(headers ?? {}).forEach(([k, v]) => response.headers.set(k, v));
      },
    },
  });
  // getClaims verifies the token (against the project's JWKS) and refreshes an expired session.
  const { data } = await supabase.auth.getClaims();
  const signedIn = !!data?.claims;

  const redirect = (to: URL) => {
    const r = NextResponse.redirect(to);
    response.cookies.getAll().forEach((c) => r.cookies.set(c)); // keep refreshed cookies
    return r;
  };
  if (!signedIn && !isAuthPath(path)) {
    const to = new URL("/sign-in", request.url);
    to.searchParams.set("next", path + request.nextUrl.search);
    return redirect(to);
  }
  if (signedIn && SIGNED_OUT_ONLY.includes(path)) return redirect(new URL("/", request.url));
  return response;
}

export const config = {
  // Pages only: not Next internals, the mock API, or static files (fonts, images, favicon).
  matcher: ["/((?!_next/|api/|favicon.ico|.*\\.(?:png|jpg|jpeg|svg|webp|ico|woff2?)$).*)"],
};
