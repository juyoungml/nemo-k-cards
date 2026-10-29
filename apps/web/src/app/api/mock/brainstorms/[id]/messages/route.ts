import { bad, body, notFound } from "@/mock/http";
import { materialRefs, sendMessage } from "@/mock/server";

export async function POST(req: Request, ctx: RouteContext<"/api/mock/brainstorms/[id]/messages">) {
  const { text = "", urls = [], material_ids = [] } = await body<{ text: string; urls: string[]; material_ids: string[] }>(req);
  if (!text.trim() && !urls.length && !material_ids.length) return bad("text, urls or material required", 422);
  const attached = materialRefs(material_ids);
  if (!attached) return bad("unknown Source material", 422);
  await new Promise((r) => setTimeout(r, 1200)); // planner latency
  const d = sendMessage((await ctx.params).id, text.trim(), urls, attached);
  return d ? Response.json(d) : notFound("brainstorm not found");
}
