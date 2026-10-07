import { bad } from "@/mock/http";
import { generateFromDraft } from "@/mock/server";

export async function POST(_req: Request, ctx: RouteContext<"/api/mock/drafts/[id]/generate">) {
  const r = generateFromDraft((await ctx.params).id);
  return "job" in r ? Response.json(r.job, { status: 201 }) : bad(r.error, 409);
}
