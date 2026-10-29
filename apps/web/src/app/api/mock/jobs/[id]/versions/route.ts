import { body, dynamic, notFound } from "@/mock/http";
import { listVersions, saveVersion } from "@/mock/server";
import type { DeckEdit } from "@/lib/types";

export async function GET(_req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/versions">) {
  await dynamic();
  const versions = listVersions((await ctx.params).id);
  return versions ? Response.json(versions) : notFound("job not found");
}

export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/versions">) {
  await dynamic(600); // the API re-renders the changed slides
  const r = saveVersion((await ctx.params).id, await body<DeckEdit>(req));
  return "error" in r ? Response.json({ detail: r.error }, { status: r.code }) : Response.json(r.job, { status: 201 });
}
