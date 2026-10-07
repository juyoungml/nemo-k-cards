import { body, dynamic, notFound } from "@/mock/http";
import { getDraft, patchDraft } from "@/mock/server";
import type { Draft } from "@/lib/types";

export async function GET(_req: Request, ctx: RouteContext<"/api/mock/drafts/[id]">) {
  await dynamic();
  const d = getDraft((await ctx.params).id);
  return d ? Response.json(d) : notFound("draft not found");
}

export async function PATCH(req: Request, ctx: RouteContext<"/api/mock/drafts/[id]">) {
  const d = patchDraft((await ctx.params).id, await body<Draft>(req));
  return d ? Response.json(d) : notFound("draft not found");
}
