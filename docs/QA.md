# Admin QA (mock backend)

백엔드 없이 Admin 전체 흐름을 테스트하는 방법입니다. `NEXT_PUBLIC_API_URL`이 없으면 Admin은 같은 앱 안의 **mock API**(`/api/mock/*`, 구현: `apps/web/src/mock/server.ts`)를 사용합니다. mock API는 SPEC §12 계약을 그대로 따르기 때문에, FastAPI가 준비되면 URL만 바꾸면 됩니다.

## 실행

```bash
cd apps/web && pnpm install && pnpm dev
```

http://localhost:3000 을 열면 사이드바 아래에 노란 **Mock API** 박스가 보입니다. **Reset mock data**를 누르면 seed 상태로 돌아갑니다.

> 실제 백엔드로 전환: `NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm dev`

## 동작 방식

- 상태는 서버 메모리에 있습니다. dev 서버를 재시작하거나 Reset하면 초기화됩니다.
- 파이프라인은 job 생성 시각부터의 **경과 시간**으로 진행됩니다. Research 3s → Verify 2s → Copy 2s → Render 2.5s → QA 1.5s → Review 2s, 총 약 13초. 승인 후 게시는 2.5초입니다.
- 진행 상황은 SSE(`/api/mock/jobs/{id}/events`)로 전달됩니다. 다른 화면은 2~5초마다 다시 불러옵니다(polling).

## 시나리오

New Job 화면의 **QA scenario** 드롭다운(mock 모드에서만 보임)이나 API의 `scenario` 필드로 선택합니다. fixture 위치: `apps/web/src/mock/scenarios/*.json`

| scenario | 최종 상태 | 확인할 것 |
|---|---|---|
| `good` | `READY_FOR_REVIEW` | 5개 행사 중 2개가 링크 검증으로 제외(lookalike, 404), tone warn 1건, 승인 → 게시 |
| `bad` | `REJECTED` | 민감 표현(Tank Day / 5·18), PII(전화번호), 폰트 깨짐, 프롬프트 인젝션. Policy Log에 DELETE·POST `policy_denied` 2건 |
| `fail` | `FAILED` | Render 단계에서 timeout, 에러 메시지 표시, Review 링크 없음 |

## 체크리스트

### Dashboard `/`
- [ ] 메트릭 4개, 채널 4개(@whatsonkorea만 Live)가 보인다
- [ ] 새 job이 "Recent card news" 맨 위에 나타나고, 상태 배지가 자동으로 바뀐다
- [ ] 게시하면 "Published this week" 숫자가 1 늘어난다

### New Job · Quick `/jobs/new`
- [ ] 프리셋 버튼을 누르면 요청란이 채워진다
- [ ] 3자 미만이면 Run agent가 비활성화된다
- [ ] `good` 실행 → 단계가 순서대로 ✓, 로그가 실시간으로 쌓이고, 끝나면 **Open in Review** 버튼이 보인다
- [ ] `bad` 실행 → 로그에 `policy_denied`(노란색), 최종 Rejected
- [ ] `fail` 실행 → Render 단계에 ✗, 빨간 에러 박스, Review 버튼 없음

### New Job · Brainstorm `/jobs/new/brainstorm`
- [ ] 화면에 들어오면 망원 야시장 샘플 초안이 만들어진다
- [ ] 정상 URL을 보내면 "링크 확인했어요 ✓", 의심 URL(`bit.ly`, `.xyz`, `visitkorea-…`)을 보내면 "⚠️ 링크 검증에서 막혔어요"
- [ ] "팁 추가"를 보내면 CTA 앞에 tips 슬라이드가 생긴다 / "다른 앵글"을 보내면 앵글 3개가 바뀐다
- [ ] 앵글 선택, 타깃·톤 토글, 아웃라인 제목 수정·순서 변경이 새로고침 없이 반영된다
- [ ] 한글 입력 중(조합 중) Enter로는 전송되지 않는다
- [ ] **Generate** → `/jobs/new`로 이동, 로그 첫 줄 "Research skipped", 파이프라인이 Verify부터 시작
- [ ] 앵글을 선택하지 않으면 Generate가 비활성화된다

### Review `/review`, `/review/{id}`
- [ ] 목록에 mode(quick/brainstorm)와 이슈 개수가 보인다
- [ ] 상세: 썸네일을 누르면 슬라이드가 바뀐다, 제외된 행사는 흐리게 + `Excluded · …` 배지
- [ ] `good`: Approve → "publishing…" → Published + **View post** 링크 (mock permalink)
- [ ] `bad`/`fail`: Approve·Reject 버튼이 비활성화된다
- [ ] Reject → Rejected, Regenerate → 같은 요청으로 새 job을 만들고 `/jobs/new`로 이동

### Policy Log `/policy`
- [ ] `bad` job의 sandbox(`agent-<id>`) 이벤트가 맨 위에 빨간색으로 강조된다
- [ ] Denied 숫자가 늘어난다

## API로 직접 확인 (curl)

```bash
B=http://localhost:3000/api/mock
curl -s -X POST $B/jobs -H 'content-type: application/json' -d '{"prompt":"qa test","scenario":"bad"}'
curl -sN $B/jobs/<id>/events            # SSE
curl -s -X POST $B/jobs/<id>/approve -H 'content-type: application/json' -d '{"caption":"..."}'
curl -s -X POST $B/reset
```

## 알려진 제약

- 레이아웃은 데스크톱 폭(≥ 1280px) 기준이라 좁은 화면에서는 가로 스크롤이 생깁니다.
- mock 상태는 프로세스 메모리에 있으므로 인스턴스가 여러 개면 서로 공유되지 않습니다(배포 시 1개 인스턴스 기준).
- 이미지 렌더링은 HTML 미리보기로 대체합니다(실제 PNG는 백엔드 렌더러 몫).
