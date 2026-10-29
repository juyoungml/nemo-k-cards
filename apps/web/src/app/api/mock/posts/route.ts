import { dynamic } from "@/mock/http";
import { listPosts } from "@/mock/server";

export async function GET(req: Request) {
  await dynamic();
  return Response.json(listPosts(new URL(req.url).searchParams.get("job_id")));
}
