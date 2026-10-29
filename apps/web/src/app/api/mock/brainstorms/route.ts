import { body } from "@/mock/http";
import { createBrainstorm } from "@/mock/server";

export async function POST(req: Request) {
  const { sample = true } = await body<{ sample: boolean }>(req);
  return Response.json(createBrainstorm(sample), { status: 201 });
}
