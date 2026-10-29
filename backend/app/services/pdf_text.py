"""Text out of an uploaded PDF, in a child process (app/materials.py runs this file with `python -I`).

A PDF is untrusted input: a crafted one can make a parser loop or allocate without end. Running pypdf (BSD-3-Clause)
in its own short-lived process lets the host kill it on a time limit and cap its CPU and memory, which a thread can't.
Reads the PDF on stdin; writes {"pages", "text", "truncated"} or {"error"} as JSON on stdout.

    python -I pdf_text.py <max_pages> <max_chars>  < file.pdf
"""

import io
import json
import sys


def extract(data: bytes, max_pages: int, max_chars: int) -> dict:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return {"error": "the PDF is password-protected"}
        n = len(reader.pages)
    except (PdfReadError, ValueError, KeyError, TypeError) as e:
        return {"error": f"not a readable PDF ({type(e).__name__})"}
    parts, total, truncated = [], 0, n > max_pages
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            t = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001  one broken page must not lose the others
            t = f"[page {i + 1} unreadable: {type(e).__name__}]"
        t = " ".join(t.split())
        if not t:
            continue
        parts.append(f"[page {i + 1}] {t}")
        total += len(parts[-1]) + 1
        if total >= max_chars:
            truncated = True
            break
    text = "\n".join(parts)
    if len(text) > max_chars:
        text, truncated = text[:max_chars], True
    return {"pages": n, "text": text, "truncated": truncated}


def _limit_resources() -> None:
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
        resource.setrlimit(resource.RLIMIT_AS, (1 << 30, 1 << 30))  # 1 GB; not enforced on macOS
    except (ImportError, ValueError, OSError):
        pass


if __name__ == "__main__":
    _limit_resources()
    out = extract(sys.stdin.buffer.read(), int(sys.argv[1]), int(sys.argv[2]))
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
