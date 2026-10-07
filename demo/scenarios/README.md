# Demo scenarios

데모 재현용 고정 데이터입니다 (SPEC §7).

- **Admin mock 백엔드 fixture**: `apps/web/src/mock/scenarios/{good,bad,fail}.json` — QA 방법은 [docs/QA.md](../../docs/QA.md)
- **FastAPI `DEMO_MODE=fixture`**: 이 폴더를 읽습니다. 시나리오 이름은 mock과 같고, `POST /jobs {prompt, scenario}`로 고릅니다(기본 `good`). 에이전트 단계만 fixture로 대체하고 Verify·Render·QA는 실제로 돌립니다.

| 폴더 | 최종 상태 | 내용 |
|---|---|---|
| `good/` | `READY_FOR_REVIEW` | 행사 5개 중 2개 링크 검증으로 제외(lookalike, 404), tone warn 1건 |
| `bad/` | `REJECTED` | 민감 표현(Tank Day / 5·18), 슬라이드 속 전화번호(host QA가 검출), tofu, 인젝션 페이지. `policy_events.json`이 이 job의 Policy Log로 들어감 |
| `fail/` | `FAILED` | `fail.json`의 `fail_at` 단계(render)에서 timeout |
| `brainstorm/` | `READY_FOR_REVIEW` | Brainstorm Generate용. 샘플 Draft(망원 야시장) 출처의 링크 검증 결과 + review |

- 시나리오 폴더에 없는 파일은 `good/`에서 가져옵니다(예: `fail/`은 `fail.json`만 있음).
- `verification.json`의 결과는 그 URL에만 쓰고, fixture에 없는 URL은 실제로 링크를 검사합니다. 그래서 Brainstorm에서 직접 넣은 URL도 검증됩니다.
- 파일 형식: `briefs.json` `list[EventBrief]` · `verification.json` `VerificationReport` · `deck.json` `CardDeck` · `review.json` `ReviewVerdict` · `policy_events.json` `PolicyEvent` (`time`·`sandbox`·`job_id` 제외) · `fail.json` `{fail_at, error, log}`
