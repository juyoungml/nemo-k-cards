import { dynamic } from "@/mock/http";
import { metrics } from "@/mock/server";

export async function GET() {
  await dynamic();
  return Response.json(metrics());
}
