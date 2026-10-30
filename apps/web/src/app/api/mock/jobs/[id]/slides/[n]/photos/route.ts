import { dynamic } from "@/mock/http";
import { slidePhotos } from "@/mock/server";

// Like the API (#23): what an event slide's photo can be swapped to (stock thumbnails aren't served by the mock).
export async function GET(_req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/slides/[n]/photos">) {
  await dynamic();
  const { id, n } = await ctx.params;
  const r = slidePhotos(id, Number(n));
  return "error" in r ? Response.json({ detail: r.error }, { status: r.code }) : Response.json(r);
}
