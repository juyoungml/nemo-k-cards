// Which Organization the Admin is working in, for people who are Members of several. Kept per browser and sent
// as `X-Org-Id` on every API call; the API checks it against the caller's memberships (403 if they aren't one)
// and uses their oldest membership when it's absent. It is a choice, never a grant.

const KEY = "wok.org";

export function activeOrgId(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setActiveOrgId(id: string | null) {
  try {
    if (id) window.localStorage.setItem(KEY, id);
    else window.localStorage.removeItem(KEY);
  } catch {
    // storage blocked: the API falls back to the oldest membership
  }
}

/** Header for API calls (empty when no choice was made). */
export function orgHeader(): Record<string, string> {
  const id = activeOrgId();
  return id ? { "x-org-id": id } : {};
}
