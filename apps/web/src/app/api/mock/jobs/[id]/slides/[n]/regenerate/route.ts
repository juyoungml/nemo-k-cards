import { body, dynamic } from "@/mock/http";
import { regenerateSlide } from "@/mock/server";

// Like the API (#23): the slide rewritten (by the copywriter there; a fixed rewrite here) as a new Deck version.
export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/slides/[n]/regenerate">) {
  await dynamic(1200);
  const { id, n } = await ctx.params;
  const r = regenerateSlide(id, Number(n), (await body<{ base_version: number; instruction?: string }>(req)) as { base_version: number; instruction?: string });
  return "error" in r ? Response.json({ detail: r.error }, { status: r.code }) : Response.json(r.job, { status: 201 });
}
