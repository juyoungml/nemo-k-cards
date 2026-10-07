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

## 배포 (Admin · Railway)

- URL: https://admin-production-3db0.up.railway.app (프로젝트 `whatsonkorea-admin`, 서비스 `admin`)
- 설정: [apps/web/railway.json](apps/web/railway.json) (Railpack, `pnpm build` / `pnpm start`, Node 22)
- 현재 **mock 모드**입니다(`NEXT_PUBLIC_API_URL` 미설정 → `/api/mock`). mock 상태는 메모리에 있으니 인스턴스는 1개로 유지하세요.

```bash
cd apps/web && railway link   # 최초 1회: whatsonkorea-admin / admin 선택
railway up --service admin --ci
```

실제 백엔드로 전환하려면 `railway variables --set NEXT_PUBLIC_API_URL=https://<fastapi-host>`를 실행하고 다시 배포하세요(빌드 시점에 값이 들어감). FastAPI CORS에 Admin 도메인도 추가해야 합니다.

## 웹 발표

- [5분 발표 슬라이드](https://juyoung.site/nemo-k-cards/)
- [관객 체험 안내](https://juyoung.site/nemo-k-cards/try/)
- 소스: `presentation/`. `main`에 변경을 푸시하면 GitHub Actions가 이 폴더만 Pages에 배포합니다.
