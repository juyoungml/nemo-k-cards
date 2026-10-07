import { bad, body } from "@/mock/http";
import { decide } from "@/mock/server";

export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/approve">) {
  const { caption } = await body<{ caption: string }>(req);
  const r = decide((await ctx.params).id, "approve", { caption });
  return "error" in r ? bad(r.error!, r.code) : Response.json(r.job);
}
