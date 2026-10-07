"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

/** Minimal data hook: fetch on mount, optional polling, manual reload. */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[] = [], pollMs?: number) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<Error>();
  const fnRef = useRef(fn);
  useLayoutEffect(() => {
    fnRef.current = fn;
  });

  const reload = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError(undefined);
    } catch (e) {
      setError(e as Error);
    }
  }, []);

  useEffect(() => {
    void reload();
    if (!pollMs) return;
    const t = setInterval(reload, pollMs);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reload, pollMs, ...deps]);

  return { data, error, reload, setData };
}

export const TERMINAL_STATUSES = ["READY_FOR_REVIEW", "REJECTED", "PUBLISHED", "FAILED"];

/**
 * Keep a running job fresh. SSE is the fast path; it reconnects with backoff when the stream drops
 * (backend reload, proxy/tunnel idle timeout), and a 2s poll is the safety net so the screen can never
 * sit at "running" after the job has actually finished. Stops once the job reaches a terminal status.
 */
export function useLiveJob<T extends { id: string; status: string }>(
  job: T | undefined,
  onUpdate: (j: T) => void,
  eventsUrl: (id: string) => string,
  fetchJob: (id: string) => Promise<T>,
) {
  const cb = useRef(onUpdate);
  useLayoutEffect(() => {
    cb.current = onUpdate;
  });
  const id = job && !TERMINAL_STATUSES.includes(job.status) ? job.id : null;
  const [stale, setStale] = useState(false);

  useEffect(() => {
    if (!id) return;
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let attempt = 0;
    let closed = false;
    const accept = (j: T) => {
      if (j.id !== id) return; // never let a stale stream overwrite a newer job
      setStale(false);
      cb.current(j);
    };
    const connect = () => {
      es = new EventSource(eventsUrl(id));
      es.onopen = () => {
        attempt = 0;
      };
      es.onmessage = (e) => accept(JSON.parse(e.data) as T);
      es.onerror = () => {
        es?.close();
        if (closed) return;
        setStale(true);
        retry = setTimeout(connect, Math.min(10_000, 1000 * 2 ** attempt++));
      };
    };
    connect();
    // Some proxies hold SSE until the stream ends (e.g. Cloudflare quick tunnels), so poll as well while it runs.
    const poll = setInterval(() => fetchJob(id).then(accept).catch(() => setStale(true)), 2000);
    return () => {
      closed = true;
      es?.close();
      clearTimeout(retry);
      clearInterval(poll);
    };
  }, [id, eventsUrl, fetchJob]);

  return { live: !!id, stale: !!id && stale };
}
