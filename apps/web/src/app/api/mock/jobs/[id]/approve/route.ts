import { bad, body } from "@/mock/http";
import { decide } from "@/mock/server";

// Like the API: approval pins a Deck version and edits nothing (a caption change is a Deck edit, #19).
export async function POST(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/approve">) {
  const { override_reason, deck_version, account_id, ...rest } = await body<{
    override_reason?: string;
    deck_version: number;
    account_id?: string;
    caption?: unknown;
  }>(req);
  if ("caption" in rest) return bad("approve takes no caption: edit the Deck instead (a new version)", 422);
  const r = decide((await ctx.params).id, "approve", { overrideReason: override_reason, deckVersion: deck_version, accountId: account_id });
  return "error" in r ? bad(r.error!, r.code) : Response.json(r.job);
}
