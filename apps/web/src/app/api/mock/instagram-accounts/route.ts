import { dynamic } from "@/mock/http";
import { instagramAccounts } from "@/mock/server";

export async function GET() {
  await dynamic();
  return Response.json(instagramAccounts());
}
