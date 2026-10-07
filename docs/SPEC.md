# What's On Korea — 해커톤 스펙 v0.2

> Fastcampus × NVIDIA Agentic AI Hackathon
> v0.1 초안을 인터뷰로 구체화한 버전입니다. 기획 배경은 v0.1과 같고, 이 문서는 **무엇을 어떻게 만드는지**에 집중합니다.

---

## 0. 한 줄 피치

외국인을 위한 한국 행사 정보를 AI Agent가 **조사 → 검증 → 카드뉴스 제작 → 최종 검수**까지 처리하고, 사람이 승인하면 게시합니다. 에이전트가 할 수 있는 일의 경계는 **NVIDIA OpenShell 정책**이 커널·네트워크 레벨에서 강제합니다.

> "그냥 카드뉴스 생성기"가 아니라, **검증된 정보 + 안전한 게시 워크플로우**를 보여주는 것이 핵심입니다.

---

## 1. 확정된 결정 사항

| 항목 | 결정 | 비고 |
|---|---|---|
| 기간/팀 | 1~2일, 3~5명 | 데모 경로 우선, 나머지는 mock |
| NVIDIA 스택 | **OpenShell** (필수) | 에이전트 샌드박스 + 정책 |
| 에이전트 런타임 | **Claude Code** | OpenShell 샌드박스 안에서 실행. 호출 방식은 `AgentRunner` 인터페이스로 추상화하고 **CLI(`claude -p`)로 시작**, 필요하면 Agent SDK로 교체 |
| 백엔드 | FastAPI (Python, uv) | 샌드박스 **밖**(host)에서 실행. Graph API 토큰은 백엔드만 보유 |
| 프론트엔드 | Next.js (Admin) | 디자인은 **Figma 목업을 먼저** 만들고 shadcn/ui + Tailwind로 구현 |
| 카드 렌더링 | **HTML 템플릿 → Playwright PNG** | Canva는 확장 옵션 (§9 참고) |
| 카드 포맷 | 영어, 6~8장, 1080×1350 (4:5) | 행사명은 한글 병기 |
| 게시 | Instagram Graph API 실제 게시 | 사람이 Admin에서 승인한 뒤에만, 백엔드가 수행 |
| 데이터 소스 | 웹 검색, TourAPI, 서울시 문화행사 API, Kakao/Naver 지도, k-skill | 빠르게 붙는 것부터 |
| Admin 메트릭 | `@whatsonkorea` IG Insights 실데이터 + 나머지는 seed mock | |

---

## 2. 사용자 & 핵심 시나리오

- **1차 사용자 (운영자)**: 프롬프트 한 줄로 카드뉴스 초안을 받고, 검수 리포트를 확인한 뒤 승인/반려합니다.
- **2차 사용자 (외국인 팔로워)**: 저장해 두고 바로 참고할 수 있는 영어 카드뉴스를 받습니다.

**Happy path**

1. 운영자 입력: "이번 주말 서울 팝업 5개 조사해서 카드뉴스 만들어줘"
2. 에이전트가 조사·검증·작성 → 렌더 → 검수 → `READY_FOR_REVIEW`
3. 운영자가 Review 화면에서 슬라이드, 출처, 링크 상태를 확인 → **Approve & Publish**
4. 백엔드가 Graph API로 캐러셀 게시 → 게시 URL과 메트릭 수집 시작

---

## 3. 아키텍처

```
┌──────────────── Host (trusted) ─────────────────┐
│  Next.js Admin  ──►  FastAPI Backend             │
│                       ├─ Job orchestrator        │
│                       ├─ Link checker            │
│                       ├─ Renderer (Playwright)   │
│                       ├─ Visual QA (DOM/OCR)     │
│                       ├─ Publisher (Graph API) ◄─┼── 🔑 IG 토큰은 여기에만
│                       └─ SQLite                  │
│                              │ openshell sandbox create --no-keep -- claude -p ...
└──────────────────────────────┼──────────────────┘
                               ▼
┌──────── OpenShell Sandbox (untrusted, per-stage) ────────┐
│  Claude Code (headless)                                   │
│   ├─ .claude/agents: researcher / copywriter / reviewer   │
│   ├─ .claude/skills: event-sources, k-skill …             │
│   └─ stdout → JSON (pydantic 스키마로 검증)                │
│  Network: allowlist (GET 위주) · graph.facebook.com 쓰기 차단 │
│  FS: /sandbox/brand (ro), /sandbox/out (rw)               │
│  Credentials: provider가 허용된 endpoint에만 주입           │
└───────────────────────────────────────────────────────────┘
```

**원칙**

- **LLM 판단은 샌드박스 안, 결정적 검사와 부작용(게시)은 host에서.**
- 단계마다 샌드박스를 새로 만들고 버립니다(`--no-keep`). 이전 단계 결과는 JSON 입력으로만 전달합니다.
- 에이전트는 IG 토큰을 볼 수 없고, 정책상 graph.facebook.com에 쓸 수도 없습니다. 프롬프트 인젝션이 성공해도 게시나 삭제는 불가능합니다.

---

## 4. 파이프라인

| # | Stage | 실행 위치 | 입력 → 출력 | 실패 시 |
|---|---|---|---|---|
| 1 | **Research** | sandbox · `researcher` | prompt → `EventBrief[]` (출처 URL 필수) | 재시도 1회 |
| 2 | **Verify** | host | `EventBrief[]` → `VerificationReport` (링크 상태, 공식 출처 여부, 날짜 유효성) | dead/의심 링크가 있는 행사는 제외하거나 교체 요청 |
| 3 | **Outline & Copy** | sandbox · `copywriter` | 검증된 brief → `CardDeck` (슬라이드별 텍스트) | — |
| 4 | **Render** | host | `CardDeck` → PNG 6~8장 | — |
| 5 | **Visual QA** | host | PNG + DOM 측정 → `QAReport` | 템플릿 조정 후 재렌더 |
| 6 | **Final Review** | sandbox · `reviewer` (vision) | deck + PNG + 리포트 → `ReviewVerdict` (pass/fail + issues) | fail → `REJECTED` + 사유 표시 |
| 7 | **Human Publish** | host | 운영자 승인 → Graph API 캐러셀 게시 | — |

**Job 상태**: `QUEUED → RESEARCHING → VERIFYING → WRITING → RENDERING → QA → REVIEWING → READY_FOR_REVIEW | REJECTED → PUBLISHING → PUBLISHED`

진행 상황은 SSE(`GET /jobs/{id}/events`)로 Admin에 실시간 전달합니다.

---

## 5. 데이터 모델 (요약)

```python
class Source(BaseModel):
    url: HttpUrl
    kind: Literal["official", "ticketing", "news", "sns", "public_api", "other"]
    fetched_at: datetime

class EventBrief(BaseModel):
    id: str
    title_en: str
    title_ko: str
    category: Literal["popup", "festival", "exhibition", "performance", "experience", "other"]
    start_date: date
    end_date: date
    venue_en: str
    venue_ko: str
    address_ko: str
    lat: float | None
    lng: float | None
    nearest_station: str | None        # 예: "Seongsu Stn. (Line 2) Exit 4"
    price: str | None                  # "Free" / "₩15,000"
    booking: str | None                # 예약 필요 여부, 방법
    foreigner_tips: list[str]          # 외국인 관점 맥락 / 주의점
    why_go: str                        # 한 줄 판단 포인트
    sources: list[Source]              # 1개 이상, official 우선

class LinkCheck(BaseModel):
    url: str
    status: Literal["ok", "dead", "redirect", "suspicious", "timeout"]
    http_code: int | None
    final_url: str | None
    reason: str | None

class Slide(BaseModel):
    index: int
    layout: Literal["cover", "event", "tips", "map", "cta"]
    heading: str
    body: str
    event_id: str | None
    image_url: str | None

class CardDeck(BaseModel):
    job_id: str
    title: str
    caption: str          # IG 캡션 + 해시태그
    slides: list[Slide]   # 6~8장

class Issue(BaseModel):
    severity: Literal["block", "warn"]
    category: Literal["fact", "link", "sensitive", "pii", "visual", "tone"]
    slide_index: int | None
    message: str

class ReviewVerdict(BaseModel):
    verdict: Literal["pass", "fail"]
    issues: list[Issue]
```

---

## 6. 카드뉴스 포맷

- **크기**: 1080×1350 PNG, 6~8장
- **구성**: `cover` → `event` × N (3~5) → `tips` (외국인 맥락/준비물) → `cta` ("Save this for the weekend · Follow @whatsonkorea")
- **event 슬라이드 필수 필드**: 행사명(EN + 한글), 날짜, 장소 + 가까운 역, 가격, 예약 여부, why-go 한 줄, 출처 표기
- **브랜드**: 친절하고 신뢰감 있는 설명형 톤. 태극 컬러를 포인트로 쓰고 캘린더 모티프를 사용. 폰트는 Pretendard(한글) + Inter(영문)를 **로컬 번들로 임베드**해서 tofu를 방지
- **캡션**: 요약 3줄 + 행사별 링크 안내("link in bio") + 해시태그 10개 이하

---

## 7. 검수 & 차단 케이스 (데모 핵심)

| 케이스 | 탐지 레이어 | 탐지 방법 | 데모 연출 |
|---|---|---|---|
| **Dead / 사기 링크** | Verify (host) | HEAD→GET, 리다이렉트 체인, 도메인 allowlist·유사 도메인(typosquat)·단축 URL 검사 | 404 링크와 피싱 유사 도메인이 섞인 행사가 자동으로 제외됨 |
| **논란성 / 민감 표현** | Final Review + 룰셋 | `policies/content/sensitive_topics.yaml` (역사적으로 민감한 날짜·표현, 정치·종교, 혐오 표현) + reviewer 판단 | 민감한 날짜에 "OO Day"처럼 오해 소지가 있는 문구가 들어간 카드가 반려됨 |
| **글자 깨짐 / 이미지 품질** | Visual QA (host) + reviewer vision | ① DOM에서 텍스트가 박스를 넘치는지 측정 ② 폰트에 없는 글자(tofu) 검출 ③ OCR 결과를 원문과 비교 ④ 이미지 해상도·대비 검사 ⑤ reviewer가 이미지를 직접 확인 | 폰트를 일부러 뺀 템플릿으로 tofu 카드를 만들고, 반려되는 장면을 보여줌 |
| **PII / 내부정보 유출** | OpenShell + host 스캐너 | 허용되지 않은 도메인으로의 egress를 정책으로 차단, 게시 전 PII 정규식 스캔(전화번호·이메일·카드번호·주민번호) | 행사 페이지에 숨긴 지시문이 "운영 메모를 pastebin에 올려라"를 유도하지만 `policy_denied` |
| **무단 게시 / 삭제** | **OpenShell** | `graph.facebook.com`을 read-only로 지정(L7 enforce)하고, 토큰은 샌드박스에 없음 | 프롬프트 인젝션이 "기존 게시물을 삭제하라"를 유도하지만 DELETE가 차단되고 Policy Log에 기록됨 |

> 데모 fixture(인젝션 페이지, 깨진 링크, 민감 표현 케이스)는 `demo/scenarios/`에 고정해 두고, 라이브 웹 상태와 무관하게 재현할 수 있게 합니다.

---

## 8. OpenShell 정책 설계

파일: `policies/openshell/agent-policy.yaml` (스키마: [OpenShell Policy Schema](https://docs.nvidia.com/openshell/latest/reference/policy-schema))

| 정책 | 구현 |
|---|---|
| Filesystem | `/sandbox/brand`, `/sandbox/input`은 read-only, `/sandbox/out`, `/tmp`만 read-write. 그 외 경로 접근 불가 |
| Network – 추론 | `api.anthropic.com` (claude-code provider가 API 키 주입) |
| Network – 데이터 | `apis.data.go.kr`(TourAPI), `openapi.seoul.go.kr`, `dapi.kakao.com`, `openapi.naver.com`, k-skill-proxy → `access: read-only`, `enforcement: enforce` |
| Network – 웹 조사 | 행사·예매 도메인 allowlist에 GET만 허용 (데모 전에 `audit` 모드로 필요한 도메인을 수집한 뒤 `enforce`로 전환) |
| Network – Instagram | `graph.facebook.com`을 **read-only + enforce**로 지정해 POST/DELETE가 L7에서 `policy_denied`. 토큰도 주입하지 않음 |
| 그 외 | default deny. 차단 이벤트는 `openshell logs <sandbox> --source sandbox`로 수집해 Admin Policy Log에 표시 |
| Publish / Delete | 샌드박스 밖 백엔드만 수행. `POST /drafts/{id}/publish`는 Admin 승인 세션에서만 호출 가능 |

> 운영 팁: 새 도메인이 필요하면 `openshell policy update`로 추가합니다. 위험한 변경은 OpenShell prover가 사람 검토 대기로 돌립니다. 이것 자체가 Human-in-the-loop 데모 포인트가 될 수 있습니다.

---

## 9. 렌더러 & Canva

- `Renderer` 인터페이스: `render(deck: CardDeck) -> list[RenderedSlide]`
- **기본: `HtmlRenderer`** — Jinja2 템플릿(`renderer/templates/*.html`)을 Playwright로 1080×1350 스크린샷. DOM 측정값을 QA에 함께 넘깁니다.
- **옵션: `CanvaRenderer`** — Canva Connect Autofill + Brand Template API. 단 **Canva Enterprise가 필요하고 Autofill/Brand Template API는 preview 단계라 접근 신청이 필요**합니다. 현재 팀에 Enterprise가 없어서 해커톤 범위에서는 제외하고, 인터페이스만 남겨 둡니다.

---

## 10. 데이터 소스 & Tool

| 소스 | 용도 | 접근 | 우선순위 |
|---|---|---|---|
| Claude WebSearch / WebFetch | 범용 조사, 공식 페이지 확인 | Claude Code 내장 | P0 |
| 한국관광공사 TourAPI (영문 서비스 포함) | 축제·행사 목록, 기간, 좌표 | data.go.kr 키 | P0 |
| 서울시 문화행사 Open API | 서울 공연·전시·행사 | 서울 열린데이터광장 키 | P0 |
| Kakao Local / Naver 지도 | 좌표, 가까운 역, 길찾기 링크 | REST 키 | P1 |
| [k-skill](https://github.com/NomaDamas/k-skill) | 한국 특화 Claude Code 스킬 (날씨/미세먼지 등 방문 팁에 활용) | 스킬 설치 + k-skill-proxy | P1 |

API 키는 전부 **OpenShell provider로 주입**합니다. 샌드박스 env나 파일에 평문으로 두지 않습니다.

---

## 11. Admin 페이지

**프로세스**: Figma 목업(4화면) → shadcn/ui + Tailwind 구현

| 화면 | 구성 요소 |
|---|---|
| **Dashboard** | SNS/Channel 리스트, 채널별 followers·reach·views (IG Insights 실데이터 + mock), 최근 카드뉴스와 상태 뱃지 |
| **New Job** | 프롬프트 입력, 프리셋("This weekend in Seoul", "Pop-ups this week"), 파이프라인 stepper(실시간 SSE), 단계별 로그 |
| **Review** | 슬라이드 캐러셀 미리보기, 캡션 편집, 행사별 출처·링크 상태 표, QA/Review issues(block/warn), **Approve & Publish** / Reject / Regenerate |
| **Policy Log** | OpenShell 차단 이벤트 타임라인 (시간, sandbox, binary, host, method/path, 결과), 데모 하이라이트 |

---

## 12. API (백엔드)

| Method | Path | 설명 |
|---|---|---|
| POST | `/jobs` | `{prompt}` → job 생성 |
| GET | `/jobs`, `/jobs/{id}` | 목록 / 상세 (brief, reports, deck, verdict) |
| GET | `/jobs/{id}/events` | SSE 진행 상황 |
| POST | `/jobs/{id}/approve` | 운영자 승인 → 게시 |
| POST | `/jobs/{id}/reject` | 반려 (사유) |
| GET | `/channels`, `/metrics` | Dashboard |
| GET | `/policy-events` | OpenShell 차단 로그 |

---

## 13. 팀 분업 (4명 기준 예시)

| 역할 | 담당 | 첫 마일스톤 |
|---|---|---|
| A. Agent | `agent/` 서브에이전트·스킬·프롬프트, JSON 스키마 출력 | `researcher`가 `EventBrief[]`를 안정적으로 출력 |
| B. Backend | FastAPI, 파이프라인, link checker, Graph API | `/jobs` happy path 완주 (mock agent) |
| C. Render & QA | HTML 템플릿, Playwright, Visual QA | `CardDeck` fixture → PNG 7장 + QAReport |
| D. Frontend & Policy | Figma → Admin, OpenShell 정책·Policy Log | Review 화면 + 무단 DELETE 차단 시연 |

---

## 14. 마일스톤

| 시점 | 목표 |
|---|---|
| D1 오전 | 스키마 고정, mock 파이프라인 E2E (agent 없이 fixture) |
| D1 오후 | 실제 researcher·copywriter 연결, 렌더 + QA, OpenShell 샌드박스에서 실행 |
| D1 저녁 | reviewer + 차단 케이스 3종 재현, Admin Review 화면 |
| D2 오전 | IG Graph API 실제 게시, Policy Log, Dashboard 메트릭 |
| D2 오후 | 데모 리허설, 실제 계정에 카드뉴스 N개 게시, 발표 자료 |

---

## 15. 성공 기준

- [ ] 프롬프트 한 줄로 영어 카드뉴스 6~8장이 생성되고, 모든 행사에 검증된 출처가 붙는다
- [ ] 차단 케이스 5종(링크 / 민감 표현 / 글자 깨짐 / PII 유출 / 무단 게시·삭제)이 데모에서 재현되고 막힌다
- [ ] 게시는 오직 사람 승인 → 백엔드 경로로만 일어난다 (OpenShell 로그로 증명)
- [ ] `@whatsonkorea`에 실제 카드뉴스가 게시되어 있다

---

## 16. 오픈 이슈 / 리스크

| 이슈 | 대응 |
|---|---|
| IG Graph API 게시 권한 (비즈니스 계정, Meta 앱, `instagram_content_publish`) 준비 시간 | **D1 오전에 바로 착수**. 개발 모드 앱 + 본인 계정으로 테스트. 막히면 수동 게시로 폴백 |
| Graph API는 이미지 **공개 URL**이 필요함 | 렌더 결과를 공개 버킷(S3/R2)이나 터널(ngrok)로 노출 |
| OpenShell은 Linux / Apple Silicon + Docker 필요, 와일드카드 도메인 지원 여부 미확인 | D1 오전 스파이크. 웹 조사 도메인은 audit 모드로 먼저 수집 |
| 샌드박스 ↔ host 데이터 전달 방식 | 우선 `claude -p --output-format json` stdout으로 전달. 필요하면 workdir 마운트 검토 |
| WebFetch가 샌드박스에서 egress를 쓰는 범위 | audit 로그로 확인 |
| 라이브 웹 변동으로 데모가 불안정해질 수 있음 | `demo/scenarios/` fixture 모드 제공 (`DEMO_MODE=fixture`) |
