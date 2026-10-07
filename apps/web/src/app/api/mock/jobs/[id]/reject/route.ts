import { bad, body } from "@/mock/http";
import { decide } from "@/mock/server";

export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/reject">) {
  const { reason } = await body<{ reason: string }>(req);
  const r = decide((await ctx.params).id, "reject", { reason });
  return "error" in r ? bad(r.error!, r.code) : Response.json(r.job);
}
