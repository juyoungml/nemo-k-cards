import { body, bad, dynamic } from "@/mock/http";
import { SCENARIOS, createJob, listJobs } from "@/mock/server";
import type { Scenario } from "@/lib/types";

export async function GET() {
  await dynamic();
  return Response.json(listJobs());
}

export async function POST(req: Request) {
  const { prompt, scenario = "good" } = await body<{ prompt: string; scenario: Scenario }>(req);
  if (!prompt || prompt.trim().length < 3) return bad("prompt must be at least 3 characters", 422);
  if (!(scenario in SCENARIOS)) return bad(`unknown scenario: ${scenario}`, 422);
  return Response.json(createJob(prompt.trim(), scenario), { status: 201 });
}
