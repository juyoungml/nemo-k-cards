import { connection } from "next/server";

import { notFound } from "@/mock/http";
import { getJob, isTerminal } from "@/mock/server";

/** SSE: a full Job snapshot whenever it changes, until the job reaches a terminal state. */
export async function GET(req: Request, ctx: RouteContext<"/api/mock/jobs/[id]/events">) {
  await connection();
  const { id } = await ctx.params;
  if (!getJob(id)) return notFound("job not found");

  const enc = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      let last = "";
      const tick = () => {
        const job = getJob(id);
        if (!job) return close();
        const data = JSON.stringify(job);
        if (data !== last) controller.enqueue(enc.encode(`data: ${data}\n\n`));
        last = data;
        if (isTerminal(job.status)) close();
      };
      const timer = setInterval(tick, 500);
      const close = () => {
        clearInterval(timer);
        try {
          controller.close();
        } catch {}
      };
      req.signal.addEventListener("abort", close);
      tick();
    },
  });
  return new Response(stream, { headers: { "content-type": "text/event-stream", "cache-control": "no-cache" } });
}
