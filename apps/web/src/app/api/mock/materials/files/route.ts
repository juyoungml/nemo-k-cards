import { bad } from "@/mock/http";
import { addMaterial } from "@/mock/server";

const TYPES: Record<string, string> = {
  pdf: "application/pdf",
  png: "image/jpeg",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  webp: "image/jpeg",
  txt: "text/plain",
  md: "text/plain",
  csv: "text/plain",
};

/** Attach a file. The API types it by its bytes and extracts its text; the mock goes by the extension. */
export async function POST(req: Request) {
  const form = await req.formData().catch(() => null);
  const file = form?.get("file");
  if (!(file instanceof File) || !file.size) return bad("the file is empty", 422);
  if (file.size > 10_000_000) return bad("the file is larger than 10 MB", 422);
  const type = TYPES[file.name.split(".").pop()?.toLowerCase() ?? ""];
  if (!type) return bad("only PDF, JPEG, PNG, WebP and text (.txt, .md, .csv) files can be attached", 422);
  const image = type === "image/jpeg";
  const text = type === "text/plain" ? await file.text() : image ? "" : `Text of ${file.name} (mock)`;
  const name = (file.name.split(/[\\/]/).pop() ?? "file").slice(0, 120);
  return Response.json(
    addMaterial({ kind: "file", name, content_type: type, size: file.size, pages: type === "application/pdf" ? 1 : null, text, image }),
    { status: 201 },
  );
}
