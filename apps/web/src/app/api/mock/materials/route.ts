import { bad, body } from "@/mock/http";
import { addMaterial } from "@/mock/server";

/** Attach a link or a note (the API fetches the link through safe_fetch; the mock only records it). */
export async function POST(req: Request) {
  const { kind, url = "", text = "" } = await body<{ kind: "link" | "note"; url: string; text: string }>(req);
  if (kind === "link") {
    if (!/^https?:\/\/[^\s/]+/.test(url.trim())) return bad("this link can't be used (invalid URL)", 422);
    const host = new URL(url.trim()).host;
    return Response.json(
      addMaterial({ kind, name: host, url: url.trim(), content_type: "text/html", text: `Text of ${url.trim()} (mock)` }),
      { status: 201 },
    );
  }
  if (kind === "note") {
    if (!text.trim()) return bad("the note is empty", 422);
    return Response.json(addMaterial({ kind, name: text.trim().split("\n")[0].slice(0, 60), text: text.trim() }), { status: 201 });
  }
  return bad("kind must be link or note", 422);
}
