import { body, dynamic } from "@/mock/http";
import { swapPhoto } from "@/mock/server";
import type { PhotoChoice } from "@/lib/types";

// Like the API (#23): a new Deck version with the slide's photo swapped.
export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/slides/[n]/photo">) {
  await dynamic(600);
  const { id, n } = await ctx.params;
  const r = swapPhoto(id, Number(n), (await body<{ base_version: number } & PhotoChoice>(req)) as { base_version: number } & PhotoChoice);
  return "error" in r ? Response.json({ detail: r.error }, { status: r.code }) : Response.json(r.job, { status: 201 });
}
