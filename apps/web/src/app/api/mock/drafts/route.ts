import { body } from "@/mock/http";
import { createDraft } from "@/mock/server";

export async function POST(req: Request) {
  const { sample = true } = await body<{ sample: boolean }>(req);
  return Response.json(createDraft(sample), { status: 201 });
}
