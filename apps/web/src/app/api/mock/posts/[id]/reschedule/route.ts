import { bad, body } from "@/mock/http";
import { reschedulePost } from "@/mock/server";

// Like the API (#26): a new KST time for a scheduled or on-hold Post, with the same bounds as Approve.
export async function POST(req: Request, ctx: RouteContext<"/api/mock/posts/[id]/reschedule">) {
  const { scheduled_at } = await body<{ scheduled_at: string }>(req);
  const r = reschedulePost((await ctx.params).id, scheduled_at ?? "");
  return "error" in r ? bad(r.error!, r.code) : Response.json(r.post);
}
