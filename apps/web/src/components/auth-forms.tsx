"use client";

// Sign up, sign in, password reset and first-Organization setup (Supabase Auth; see src/lib/supabase.ts).
// After any change of session we navigate with a full page load so src/proxy.ts sees the new cookies.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Brand } from "@/components/sidebar";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { AUTH_CONFIGURED, reloadTo, safeNext, signOut, supabase } from "@/lib/supabase";

const nextParam = () => safeNext(new URLSearchParams(window.location.search).get("next"));
const callback = (next: string) => `${window.location.origin}/auth/callback?next=${encodeURIComponent(next)}`;

/** Full-screen card for the signed-out pages. `data-auth-screen` hides the Admin sidebar (globals.css). */
function AuthScreen({ title, description, children, footer }: {
  title: string;
  description?: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
}) {
  return (
    <div data-auth-screen className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-sm flex-col justify-center gap-6 py-8">
      <Brand />
      <div className="space-y-5 rounded-xl border bg-card p-6">
        <div className="space-y-1">
          <h1 className="text-2xl tracking-tight">{title}</h1>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
        {AUTH_CONFIGURED ? children : (
          <Notice tone="error">
            Sign-in is not configured. Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY, then
            restart the Admin.
          </Notice>
        )}
      </div>
      {footer && <p className="text-center text-sm text-muted-foreground">{footer}</p>}
    </div>
  );
}

function Field({ label, ...input }: { label: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="block space-y-1.5">
      <span className="text-[13px] font-medium">{label}</span>
      <input
        {...input}
        className="h-10 w-full rounded-lg border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      />
    </label>
  );
}

function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  return (
    <p
      role={tone === "error" ? "alert" : "status"}
      className={tone === "error"
        ? "rounded-lg bg-danger-soft px-3 py-2 text-sm text-destructive"
        : "rounded-lg bg-muted px-3 py-2 text-sm"}
    >
      {children}
    </p>
  );
}

/** Form state shared by every screen: busy flag, one error, one info message. */
function useForm() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const [info, setInfo] = useState<string>();
  const run = (fn: () => Promise<void>) => async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(undefined);
    setInfo(undefined);
    try {
      await fn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return { busy, error, info, setInfo, run };
}

const Linked = ({ href, children }: { href: string; children: React.ReactNode }) => (
  <Link href={href} className="font-semibold text-foreground underline-offset-2 hover:underline">{children}</Link>
);

/** /auth/callback sends people back here when an email link was expired or already used. */
function LinkError() {
  return useSearchParams().has("link_error")
    ? <Notice tone="error">That link has expired or was already used. Request a new one.</Notice>
    : null;
}

export function SignInForm() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const f = useForm();

  const submit = f.run(async () => {
    const { error } = await supabase().auth.signInWithPassword({ email: email.trim(), password });
    if (error) throw new Error(error.code === "email_not_confirmed"
      ? "Confirm your email first: open the link we sent you."
      : "Wrong email or password.");
    reloadTo(nextParam());
  });

  return (
    <AuthScreen
      title="Sign in"
      description="Research, check and publish card news for your Organization."
      footer={<>New here? <Linked href="/sign-up">Create an account</Linked></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <Suspense fallback={null}>
          <LinkError />
        </Suspense>
        <Field label="Email" type="email" autoComplete="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        {f.error && <Notice tone="error">{f.error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={f.busy}>{f.busy ? "Signing in…" : "Sign in"}</Button>
        <p className="text-center text-sm"><Linked href="/forgot-password">Forgot your password?</Linked></p>
      </form>
    </AuthScreen>
  );
}

export function SignUpForm() {
  const [org, setOrg] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const f = useForm();

  const submit = f.run(async () => {
    const name = org.trim();
    const { data, error } = await supabase().auth.signUp({
      email: email.trim(),
      password,
      // Kept on the account so /welcome can finish setup if email confirmation comes in between.
      options: { data: { org_name: name }, emailRedirectTo: callback("/welcome") },
    });
    if (error) throw new Error(error.message);
    if (!data.session) {
      f.setInfo(`Check ${email.trim()} for a confirmation link. It brings you back here to finish setting up.`);
      return;
    }
    await api.createOrg(name); // the API makes you the new Organization's Admin
    reloadTo("/");
  });

  if (f.info) {
    return (
      <AuthScreen title="Check your email" footer={<Linked href="/sign-in">Back to sign in</Linked>}>
        <Notice tone="info">{f.info}</Notice>
      </AuthScreen>
    );
  }
  return (
    <AuthScreen
      title="Create an account"
      description="You become the Admin of a new Organization and can invite your team later."
      footer={<>Already have an account? <Linked href="/sign-in">Sign in</Linked></>}
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label="Organization name" required maxLength={100} autoFocus placeholder="e.g. Seoul Weekend Guide" value={org} onChange={(e) => setOrg(e.target.value)} />
        <Field label="Work email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label="Password" type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        {f.error && <Notice tone="error">{f.error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={f.busy || !org.trim()}>
          {f.busy ? "Creating…" : "Create account"}
        </Button>
      </form>
    </AuthScreen>
  );
}

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const f = useForm();
  const submit = f.run(async () => {
    const { error } = await supabase().auth.resetPasswordForEmail(email.trim(), { redirectTo: callback("/reset-password") });
    if (error) throw new Error(error.message);
    f.setInfo("If an account uses that email, a reset link is on its way. Open it in this browser.");
  });
  return (
    <AuthScreen
      title="Reset your password"
      description="We'll email you a link to choose a new one."
      footer={<Linked href="/sign-in">Back to sign in</Linked>}
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label="Email" type="email" autoComplete="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
        {f.error && <Notice tone="error">{f.error}</Notice>}
        {f.info && <Notice tone="info">{f.info}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={f.busy}>{f.busy ? "Sending…" : "Send reset link"}</Button>
      </form>
    </AuthScreen>
  );
}

export function ResetPasswordForm() {
  const [password, setPassword] = useState("");
  const f = useForm();
  const submit = f.run(async () => {
    const { data } = await supabase().auth.getSession();
    if (!data.session) throw new Error("This reset link has expired. Request a new one from the sign-in page.");
    const { error } = await supabase().auth.updateUser({ password });
    if (error) throw new Error(error.message);
    reloadTo("/");
  });
  return (
    <AuthScreen title="Choose a new password" footer={<Linked href="/forgot-password">Request a new link</Linked>}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="New password" type="password" autoComplete="new-password" required minLength={8} autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        {f.error && <Notice tone="error">{f.error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={f.busy}>{f.busy ? "Saving…" : "Save password"}</Button>
      </form>
    </AuthScreen>
  );
}

/** Signed in without an Organization (sign-up confirmed by email, or setup interrupted): create it here. */
export function WelcomeForm() {
  const [name, setName] = useState("");
  const [ready, setReady] = useState(false);
  const f = useForm();

  useEffect(() => {
    void (async () => {
      const me = await api.me().catch(() => undefined);
      if (me?.org) return reloadTo("/");
      const { data } = await supabase().auth.getUser();
      const saved = data.user?.user_metadata?.org_name;
      if (typeof saved === "string") setName(saved);
      setReady(true);
    })();
  }, []);

  const submit = f.run(async () => {
    try {
      await api.createOrg(name.trim());
    } catch (e) {
      if (!(e as Error).message.startsWith("409")) throw e; // already created (another tab): carry on
    }
    reloadTo("/");
  });

  return (
    <AuthScreen
      title="Set up your Organization"
      description="Decks, Brainstorms and Instagram accounts belong to it. You'll be its Admin."
      footer={<button type="button" className="underline-offset-2 hover:underline" onClick={signOut}>Sign out</button>}
    >
      <form onSubmit={submit} className="space-y-4">
        <Field label="Organization name" required maxLength={100} autoFocus disabled={!ready} value={name} onChange={(e) => setName(e.target.value)} />
        {f.error && <Notice tone="error">{f.error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={!ready || f.busy || !name.trim()}>
          {f.busy ? "Creating…" : "Create Organization"}
        </Button>
      </form>
    </AuthScreen>
  );
}
