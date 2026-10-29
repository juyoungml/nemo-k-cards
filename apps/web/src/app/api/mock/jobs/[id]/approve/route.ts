import { bad, body } from "@/mock/http";
import { decide } from "@/mock/server";

export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/approve">) {
  const { caption, override_reason } = await body<{ caption: string; override_reason?: string }>(req);
  const r = decide((await ctx.params).id, "approve", { caption, overrideReason: override_reason });
  return "error" in r ? bad(r.error!, r.code) : Response.json(r.job);
}
