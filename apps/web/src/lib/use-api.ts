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

/** Subscribe to a job's SSE stream; falls back to the last value on error. */
export function useEventSource<T>(url: string | null, onMessage: (v: T) => void) {
  const cb = useRef(onMessage);
  useLayoutEffect(() => {
    cb.current = onMessage;
  });
  useEffect(() => {
    if (!url) return;
    const es = new EventSource(url);
    es.onmessage = (e) => cb.current(JSON.parse(e.data) as T);
    es.onerror = () => es.close();
    return () => es.close();
  }, [url]);
}
