import { body, bad, dynamic } from "@/mock/http";
import { SCENARIOS, createJob, listJobs, materialRefs } from "@/mock/server";
import type { DeckKind, Scenario } from "@/lib/types";

export async function GET() {
  await dynamic();
  return Response.json(listJobs());
}

export async function POST(req: Request) {
  const { prompt, scenario = "good", material_ids = [], kind = null } = await body<{
    prompt: string;
    scenario: Scenario;
    material_ids: string[];
    kind?: DeckKind | null;
  }>(req);
  if (kind !== null && kind !== "event" && kind !== "guide") return bad("kind must be event or guide", 422);
  if (!prompt || prompt.trim().length < 3) return bad("prompt must be at least 3 characters", 422);
  if (!(scenario in SCENARIOS)) return bad(`unknown scenario: ${scenario}`, 422);
  const material = materialRefs(material_ids);
  if (!material) return bad("unknown Source material", 422);
  return Response.json(createJob(prompt.trim(), scenario, material, kind), { status: 201 });
}
