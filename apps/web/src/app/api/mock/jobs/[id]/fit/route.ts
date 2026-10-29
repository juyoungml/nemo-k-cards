import { bad, body, dynamic, notFound } from "@/mock/http";
import { getJob } from "@/mock/server";
import type { DeckEdit, FitResult } from "@/lib/types";

/** The API renders the slide and measures it; the mock estimates lines from the length (same limits as its save). */
export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/fit">) {
  await dynamic(250);
  const job = getJob((await ctx.params).id);
  if (!job?.deck) return notFound("job not found");
  const { slides, position } = await body<Required<DeckEdit> & { position: number }>(req);
  const s = slides?.[position ?? -1];
  if (!s) return bad("position is not one of the slides", 422);
  const layout = job.deck.slides[s.from_index]?.layout ?? "event";
  const lines = (text: string, perLine: number) => (text ? text.split("\n").reduce((n, l) => n + Math.max(1, Math.ceil(l.length / perLine)), 0) : 0);
  const blocks = [
    { selector: `${layout}.heading`, lines: lines(s.heading, 18), font_px: 76, overflow: s.heading.length > 70 },
    ...(s.body ? [{ selector: `${layout}.body`, lines: lines(s.body, 38), font_px: 36, overflow: s.body.length > 500 }] : []),
  ];
  const fit: FitResult = { position: position!, fits: !blocks.some((b) => b.overflow), small_text: false, blocks };
  return Response.json(fit);
}
