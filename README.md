# nemo-k-cards — What's On Korea
Fastcampus X NVIDIA Agentic AI Hacakthon

외국인을 위한 한국 행사 정보를 AI Agent(Claude Code)가 조사·검증·카드뉴스 제작·검수까지 처리하고, 사람이 승인하면 게시합니다. 에이전트는 NVIDIA OpenShell 샌드박스 안에서 정책의 제약을 받으며 실행됩니다.

📄 스펙: [docs/SPEC.md](docs/SPEC.md)

## 구조

```
apps/web/              Next.js Admin (Dashboard / New Job / Review / Policy Log)
backend/               FastAPI — host 측 (trusted)
  app/schemas.py         데이터 계약 (EventBrief, CardDeck, ReviewVerdict …)
  app/main.py            API 라우트
  app/pipeline/          orchestrator, agent_runner (local CLI | OpenShell)
  app/services/          link_checker, visual_qa, publisher (Graph API)
  app/renderer/          Renderer 인터페이스 + HTML 템플릿
agent/                 샌드박스에 마운트되는 Claude Code 워크스페이스
  CLAUDE.md              공통 규칙
  .claude/agents/        researcher / copywriter / reviewer
  .claude/skills/        event-sources …
policies/
  openshell/             OpenShell 샌드박스 정책 (네트워크·파일시스템)
  content/               민감 표현 룰셋 (reviewer가 사용)
infra/sandbox/         에이전트 샌드박스 이미지 Dockerfile
demo/scenarios/        데모 재현용 fixture (good / bad)
```

## 실행

```bash
cd backend && cp .env.example .env && uv sync && uv run uvicorn app.main:app --reload
```

```bash
cd apps/web && pnpm install && pnpm dev
```
