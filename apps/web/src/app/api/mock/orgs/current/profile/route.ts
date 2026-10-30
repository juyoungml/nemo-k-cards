import { body, dynamic } from "@/mock/http";
import { orgProfile, saveOrgProfile } from "@/mock/server";
import type { OrgProfile } from "@/lib/types";

export async function GET() {
  await dynamic();
  return Response.json(orgProfile());
}

export async function PUT(req: Request) {
  return Response.json(saveOrgProfile(await body<OrgProfile>(req)));
}
