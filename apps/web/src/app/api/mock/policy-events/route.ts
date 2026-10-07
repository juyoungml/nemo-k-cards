import { dynamic } from "@/mock/http";
import { policyEvents, policyStats } from "@/mock/server";

export async function GET() {
  await dynamic();
  return Response.json({ stats: policyStats(), events: policyEvents() });
}
