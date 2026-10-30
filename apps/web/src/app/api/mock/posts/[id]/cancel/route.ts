import { bad } from "@/mock/http";
import { cancelPost } from "@/mock/server";

// Like the API (#26): a scheduled or on-hold Post only.
export async function POST(_req: Request, ctx: RouteContext<"/api/mock/posts/[id]/cancel">) {
  const r = cancelPost((await ctx.params).id);
  return "error" in r ? bad(r.error!, r.code) : Response.json(r.post);
}
