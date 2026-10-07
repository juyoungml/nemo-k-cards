import { dynamic } from "@/mock/http";
import { channels } from "@/mock/server";

export async function GET() {
  await dynamic();
  return Response.json(channels());
}
