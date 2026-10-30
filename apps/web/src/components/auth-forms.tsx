"use client";

// Sign up, sign in, password reset and first-Organization setup (Supabase Auth; see src/lib/supabase.ts).
// After any change of session we navigate with a full page load so src/proxy.ts sees the new cookies.

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, useSyncExternalStore } from "react";

import { OrgProfileForm } from "@/components/org-profile-form";
import { Brand } from "@/components/sidebar";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { setActiveOrgId } from "@/lib/org";
import { ROLE_LABEL } from "@/lib/roles";
import { AUTH_CONFIGURED, reloadTo, safeNext, signOut, supabase } from "@/lib/supabase";
import type { Org } from "@/lib/types";

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

const noSubscribe = () => () => {};
/** The /invite page this sign-in or sign-up should return to, if any (read from `?next=`; none while prerendering). */
function useInviteNext(): string | undefined {
  const next = useSyncExternalStore(noSubscribe, nextParam, () => "/");
  return next.startsWith("/invite") ? next : undefined;
}

/** Keeps an invitation's `next` when someone switches from sign-in to sign-up. */
function SignUpLink() {
  const invite = useInviteNext();
  return <Linked href={invite ? `/sign-up?next=${encodeURIComponent(invite)}` : "/sign-up"}>Create an account</Linked>;
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
      footer={<>New here? <SignUpLink /></>}
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
  // Signing up to accept an invitation (/invite sends people here): join that Organization, don't make one.
  const invite = useInviteNext();

  const submit = f.run(async () => {
    const name = org.trim();
    const { data, error } = await supabase().auth.signUp({
      email: email.trim(),
      password,
      // Kept on the account so /welcome can finish setup if email confirmation comes in between.
      options: invite
        ? { emailRedirectTo: callback(invite) }
        : { data: { org_name: name }, emailRedirectTo: callback("/welcome") },
    });
    if (error) throw new Error(error.message);
    if (!data.session) {
      f.setInfo(`Check ${email.trim()} for a confirmation link. It brings you back here to finish setting up.`);
      return;
    }
    if (invite) return reloadTo(invite);
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
      description={invite
        ? "Use the email address your invitation was sent to. You join your team right after."
        : "You become the Admin of a new Organization and can invite your team later."}
      footer={<>Already have an account? <Linked href={invite ? `/sign-in?next=${encodeURIComponent(invite)}` : "/sign-in"}>Sign in</Linked></>}
    >
      <form onSubmit={submit} className="space-y-4">
        {!invite && (
          <Field label="Organization name" required maxLength={100} autoFocus placeholder="e.g. Seoul Weekend Guide" value={org} onChange={(e) => setOrg(e.target.value)} />
        )}
        <Field label="Work email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <Field label="Password" type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(e) => setPassword(e.target.value)} />
        {f.error && <Notice tone="error">{f.error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={f.busy || (!invite && !org.trim())}>
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
  const [step, setStep] = useState<"org" | "profile">("org");
  const f = useForm();

  useEffect(() => {
    void (async () => {
      const me = await api.me().catch(() => undefined);
      if (me?.org) {
        // Created it already (this tab or another): an Admin with no profile yet still gets step 2.
        const state = me.org.role === "admin" ? await api.getOrgProfile().catch(() => undefined) : undefined;
        if (state && !state.complete) return setStep("profile");
        return reloadTo("/");
      }
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
    setStep("profile");
  });

  if (step === "profile") return <WelcomeProfile />;

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

/** Step 2 of onboarding (#16): tell the agents who the Organization is. Can be skipped and filled in later on
 * the Profile page; until then the agents write as a neutral guide. */
function WelcomeProfile() {
  return (
    <div data-auth-screen className="mx-auto flex min-h-[calc(100dvh-4rem)] w-full max-w-2xl flex-col justify-center gap-6 py-8">
      <Brand />
      <div className="space-y-5 rounded-xl border bg-card p-6">
        <div className="space-y-1">
          <h1 className="text-2xl tracking-tight">Tell the agents who you are</h1>
          <p className="text-sm text-muted-foreground">
            Every deck and Brainstorm uses this profile: who you are, who you write for, and what never to say. You can
            change it any time on the Profile page.
          </p>
        </div>
        <OrgProfileForm
          initial={{ what_it_does: "", audience: "", home_region: "", usual_subjects: [], never_say: [] }}
          submitLabel="Save and continue"
          onSaved={() => reloadTo("/")}
          secondary={
            <Button type="button" variant="ghost" size="lg" onClick={() => reloadTo("/")}>
              Skip for now
            </Button>
          }
        />
      </div>
    </div>
  );
}

/**
 * /invite?token=…: joins the Organization an Admin invited this email address to (backend/app/members.py).
 * People arrive three ways: from Supabase's invite email (a new account, signed in by the tokens in the URL
 * fragment, still without a password), from a sign-in link (an existing account), or from a link the Admin sent
 * by hand (signed out: sign in or sign up first, then come back here).
 */
export function InviteForm() {
  const [step, setStep] = useState<"checking" | "signed-out" | "error" | "password" | "joined">("checking");
  const [org, setOrg] = useState<Org>();
  const [error, setError] = useState<string>();
  const [password, setPassword] = useState("");
  const f = useForm();
  const here = () => `/invite${window.location.search}`;

  useEffect(() => {
    void (async () => {
      const token = new URLSearchParams(window.location.search).get("token") ?? "";
      const hash = new URLSearchParams(window.location.hash.slice(1));
      let newAccount = false;
      if (window.location.hash) window.history.replaceState(null, "", `/invite${window.location.search}`); // no tokens left in the address bar
      if (hash.get("error_description")) {
        setError(`${hash.get("error_description")}. Ask your Admin for a new invitation.`);
        return setStep("error");
      }
      const access_token = hash.get("access_token");
      const refresh_token = hash.get("refresh_token");
      if (access_token && refresh_token) {
        const { error } = await supabase().auth.setSession({ access_token, refresh_token });
        if (error) {
          setError("That link has expired or was already used. Ask your Admin for a new invitation.");
          return setStep("error");
        }
        newAccount = hash.get("type") === "invite";
      }
      const { data } = await supabase().auth.getSession();
      if (!data.session) return setStep("signed-out");
      if (!token) {
        setError("This link is missing its invitation. Open the link from the email again.");
        return setStep("error");
      }
      try {
        const joined = await api.acceptInvitation(token);
        setActiveOrgId(joined.id); // land in the Organization they just joined
        setOrg(joined);
        setStep(newAccount ? "password" : "joined");
      } catch (e) {
        setError((e as Error).message.replace(/^\d+ /, ""));
        setStep("error");
      }
    })();
  }, []);

  const savePassword = f.run(async () => {
    const { error } = await supabase().auth.updateUser({ password });
    if (error) throw new Error(error.message);
    reloadTo("/");
  });

  const joinedAs = org ? `You joined ${org.name} as ${ROLE_LABEL[org.role]}.` : "";
  if (step === "password") {
    return (
      <AuthScreen title="Choose a password" description={`${joinedAs} Set a password to sign in next time.`}>
        <form onSubmit={savePassword} className="space-y-4">
          <Field label="Password" type="password" autoComplete="new-password" required minLength={8} autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
          {f.error && <Notice tone="error">{f.error}</Notice>}
          <Button type="submit" size="lg" className="w-full" disabled={f.busy}>{f.busy ? "Saving…" : "Save and continue"}</Button>
        </form>
      </AuthScreen>
    );
  }
  if (step === "joined") {
    return (
      <AuthScreen title={`Welcome to ${org?.name ?? "your team"}`} description={joinedAs}>
        <Button size="lg" className="w-full" onClick={() => reloadTo("/")}>Continue</Button>
      </AuthScreen>
    );
  }
  if (step === "signed-out") {
    const next = encodeURIComponent(here());
    return (
      <AuthScreen
        title="Join your team"
        description="Sign in, or create an account, with the email address this invitation was sent to."
        footer={<>New here? <Linked href={`/sign-up?next=${next}`}>Create an account</Linked></>}
      >
        <Button size="lg" className="w-full" asChild>
          <Link href={`/sign-in?next=${next}`}>Sign in to accept</Link>
        </Button>
      </AuthScreen>
    );
  }
  if (step === "error") {
    return (
      <AuthScreen
        title="Couldn't accept the invitation"
        footer={<button type="button" className="underline-offset-2 hover:underline" onClick={signOut}>Sign out and use another account</button>}
      >
        <div className="space-y-4">
          <Notice tone="error">{error}</Notice>
          <Button variant="outline" size="lg" className="w-full" onClick={() => reloadTo("/")}>Go to the Admin</Button>
        </div>
      </AuthScreen>
    );
  }
  return (
    <AuthScreen title="Joining…">
      <p className="text-sm text-muted-foreground motion-safe:animate-pulse">Checking your invitation…</p>
    </AuthScreen>
  );
}
