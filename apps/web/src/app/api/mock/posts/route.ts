import { dynamic } from "@/mock/http";
import { listPosts } from "@/mock/server";

export async function GET(req: Request) {
  await dynamic();
  const q = new URL(req.url).searchParams;
  return Response.json(listPosts(q.get("job_id"), q.get("when")));
}
