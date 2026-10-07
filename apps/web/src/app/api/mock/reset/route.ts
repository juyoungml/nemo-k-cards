import { resetStore } from "@/mock/server";

/** QA helper: restore the seed state. */
export async function POST() {
  resetStore();
  return Response.json({ ok: true });
}
