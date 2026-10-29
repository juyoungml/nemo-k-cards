import { dynamic, notFound } from "@/mock/http";
import { getMaterial } from "@/mock/server";

export async function GET(_req: Request, ctx: RouteContext<"/api/mock/materials/[id]">) {
  await dynamic();
  const m = getMaterial((await ctx.params).id);
  return m ? Response.json(m) : notFound("material not found");
}
