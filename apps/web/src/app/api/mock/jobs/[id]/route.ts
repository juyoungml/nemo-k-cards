import { dynamic, notFound } from "@/mock/http";
import { getJob } from "@/mock/server";

export async function GET(_req: Request, ctx: RouteContext<"/api/mock/jobs/[id]">) {
  await dynamic();
  const job = getJob((await ctx.params).id);
  return job ? Response.json(job) : notFound("job not found");
}
