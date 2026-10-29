"use client";

// Roles in the Admin (GLOSSARY "Role"). The API enforces every Role on every route (backend/app/auth.py); this
// only hides or disables what the signed-in Member can't do, so nobody clicks a button that would get a 403.

import { useEffect, useState } from "react";

import { IS_MOCK, api } from "./api";
import { activeOrgId, setActiveOrgId } from "./org";
import { isAuthPath } from "./supabase";
import type { Me, Role } from "./types";

export const ROLE_LABEL: Record<Role, string> = { viewer: "Viewer", editor: "Editor", approver: "Approver", admin: "Admin" };
export const ROLE_HINT: Record<Role, string> = {
  viewer: "Sees everything",
  editor: "Also starts Jobs and Brainstorms and edits Decks",
  approver: "Also approves and rejects Decks",
  admin: "Also manages Members and reads the audit log",
};
export const ROLES: Role[] = ["viewer", "editor", "approver", "admin"];
const RANK: Record<Role, number> = { viewer: 0, editor: 1, approver: 2, admin: 3 };

export const atLeast = (role: Role | undefined, min: Role) => role !== undefined && RANK[role] >= RANK[min];

// One /me request per page load, shared by every component that asks.
let pending: Promise<Me | undefined> | undefined;
function loadMe(): Promise<Me | undefined> {
  if (IS_MOCK || (typeof window !== "undefined" && isAuthPath(window.location.pathname))) return Promise.resolve(undefined);
  pending ??= api.me().then((me) => {
    // The stored choice is gone (removed from that Organization): follow what the API acts for now.
    if (activeOrgId() && me.org?.id !== activeOrgId()) setActiveOrgId(me.org?.id ?? null);
    return me;
  }).catch(() => {
    pending = undefined;
    return undefined;
  });
  return pending;
}

/**
 * The signed-in person and the Role they act with in the current Organization. The mock API (no sign-in) acts as
 * an Admin so QA can reach every screen. `role` is undefined while loading: callers treat that as "not yet".
 */
export function useMe(): { me?: Me; role?: Role; loading: boolean } {
  const [me, setMe] = useState<Me>();
  const [loading, setLoading] = useState(!IS_MOCK);
  useEffect(() => {
    let live = true;
    void loadMe().then((m) => {
      if (!live) return;
      setMe(m);
      setLoading(false);
    });
    return () => {
      live = false;
    };
  }, []);
  return { me, role: IS_MOCK ? "admin" : me?.org?.role, loading };
}
