import { bad, body, notFound } from "@/mock/http";
import { sendMessage } from "@/mock/server";

export async function POST(req: Request, ctx: RouteContext<"/api/mock/drafts/[id]/messages">) {
  const { text = "", urls = [] } = await body<{ text: string; urls: string[] }>(req);
  if (!text.trim() && !urls.length) return bad("text or urls required", 422);
  await new Promise((r) => setTimeout(r, 1200)); // planner latency
  const d = sendMessage((await ctx.params).id, text.trim(), urls);
  return d ? Response.json(d) : notFound("draft not found");
}
