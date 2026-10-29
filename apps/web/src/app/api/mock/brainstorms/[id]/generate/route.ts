import { bad } from "@/mock/http";
import { generateFromBrainstorm } from "@/mock/server";

export async function POST(_req: Request, ctx: RouteContext<"/api/mock/brainstorms/[id]/generate">) {
  const r = generateFromBrainstorm((await ctx.params).id);
  return "job" in r ? Response.json(r.job, { status: 201 }) : bad(r.error, 409);
}
