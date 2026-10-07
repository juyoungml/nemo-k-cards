import { connection } from "next/server";

/** Mark a GET handler dynamic (cacheComponents would otherwise prerender it) and add mock latency. */
export async function dynamic(ms = 150) {
  await connection();
  await new Promise((r) => setTimeout(r, ms));
}

export const notFound = (what = "not found") => Response.json({ detail: what }, { status: 404 });
export const bad = (detail: string, status = 400) => Response.json({ detail }, { status });

export async function body<T>(req: Request): Promise<Partial<T>> {
  try {
    return (await req.json()) as Partial<T>;
  } catch {
    return {};
  }
}
