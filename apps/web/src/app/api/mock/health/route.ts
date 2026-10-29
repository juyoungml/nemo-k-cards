import { dynamic } from "@/mock/http";

export async function GET() {
  await dynamic(0);
  return Response.json({ ok: true, publish_mode: "mock", demo_mode: "fixture" });
}
