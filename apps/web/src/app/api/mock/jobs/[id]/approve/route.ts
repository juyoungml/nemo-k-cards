import { bad, body } from "@/mock/http";
import { decide } from "@/mock/server";

export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/approve">) {
  const { caption, override_reason, account_id } = await body<{ caption: string; override_reason?: string; account_id?: string }>(req);
  const r = decide((await ctx.params).id, "approve", { caption, overrideReason: override_reason, accountId: account_id });
  return "error" in r ? bad(r.error!, r.code) : Response.json(r.job);
}
