# Demo scenarios

데모 재현용 고정 데이터입니다 (SPEC §7).

- **Admin mock 백엔드 fixture (정본)**: `apps/web/src/mock/scenarios/{good,bad,fail}.json` — QA 방법은 [docs/QA.md](../../docs/QA.md)
- 백엔드 `DEMO_MODE=fixture`도 같은 JSON을 읽게 하면 프론트와 백엔드가 같은 데모 데이터를 봅니다. (필드: `briefs`, `verification`, `deck`, `issues`, `log`, `policy_events`)
- `bad`에 쓸 프롬프트 인젝션 페이지가 필요하면 이 폴더에 HTML로 둡니다.
