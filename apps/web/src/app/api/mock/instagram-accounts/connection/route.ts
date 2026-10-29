import { dynamic } from "@/mock/http";
import { instagramConnection } from "@/mock/server";

export async function GET() {
  await dynamic();
  return Response.json(instagramConnection());
}
