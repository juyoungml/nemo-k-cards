import { body, bad, dynamic } from "@/mock/http";
import { SCENARIOS, createJob, listJobs, materialRefs } from "@/mock/server";
import type { Scenario } from "@/lib/types";

export async function GET() {
  await dynamic();
  return Response.json(listJobs());
}

export async function POST(req: Request) {
  const { prompt, scenario = "good", material_ids = [] } = await body<{ prompt: string; scenario: Scenario; material_ids: string[] }>(req);
  if (!prompt || prompt.trim().length < 3) return bad("prompt must be at least 3 characters", 422);
  if (!(scenario in SCENARIOS)) return bad(`unknown scenario: ${scenario}`, 422);
  const material = materialRefs(material_ids);
  if (!material) return bad("unknown Source material", 422);
  return Response.json(createJob(prompt.trim(), scenario, material), { status: 201 });
}
