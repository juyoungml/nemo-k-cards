import { body, dynamic, notFound } from "@/mock/http";
import { getBrainstorm, patchBrainstorm } from "@/mock/server";
import type { Brainstorm } from "@/lib/types";

export async function GET(_req: Request, ctx: RouteContext<"/api/mock/brainstorms/[id]">) {
  await dynamic();
  const d = getBrainstorm((await ctx.params).id);
  return d ? Response.json(d) : notFound("brainstorm not found");
}

export async function PATCH(req: Request, ctx: RouteContext<"/api/mock/brainstorms/[id]">) {
  const d = patchBrainstorm((await ctx.params).id, await body<Brainstorm>(req));
  return d ? Response.json(d) : notFound("brainstorm not found");
}
