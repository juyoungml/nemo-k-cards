<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/hero-dark.svg">
  <img src="docs/assets/brand/hero-light.svg" alt="Nemo K Cards: 한국 문화 정보를 영어 카드뉴스로" width="100%">
</picture>

<p align="center"><a href="README.en.md">English</a> · <a href="https://www.instagram.com/whatsonkorea/">Instagram</a> · <a href="https://juyoung.site/nemo-k-cards/Nemo-K-Cards-Presentation.pdf">발표 PDF</a> · <a href="CONTRIBUTING.md">기여하기</a></p>

Nemo K Cards는 한국 행사 정보를 조사하고 출처를 확인해, Instagram에 올릴 영어 카드뉴스를 만드는 AI 에이전트입니다. 운영자가 카드를 검토하고 승인하면 백엔드가 게시합니다.

카드뉴스는 [@whatsonkorea](https://www.instagram.com/whatsonkorea/)에 게시합니다. Claude Code가 조사·기획·작성·검수를 맡고, NVIDIA OpenShell이 에이전트의 파일과 네트워크 접근을 제한합니다. 게시 토큰은 백엔드에서 관리합니다.

Fastcampus × NVIDIA Agentic AI Hackathon 2026에서 시작한 초기 프로젝트입니다.

## 결과물

<p align="center">
  <a href="https://www.instagram.com/p/DeL2WwdGQP5/"><img src="docs/assets/cards/slow-seoul.jpg" width="31%" alt="서울의 여유로운 하루를 소개하는 카드"></a>
  <a href="https://www.instagram.com/p/DeL2WwdGQP5/"><img src="docs/assets/cards/seoul-forest.jpg" width="31%" alt="서울숲 카드뉴스"></a>
  <a href="https://www.instagram.com/p/DeL2e1vmXz4/"><img src="docs/assets/cards/seongsu.jpg" width="31%" alt="크리에이티브 성수 행사 카드"></a>
</p>

2026년 10월 7일 게시한 카드뉴스입니다. 모든 카드가 자동 파이프라인으로 만들어진 것은 아닙니다. 제작에 사용한 자료와 확인한 내용은 [출처와 검증 범위](docs/SHOWCASE.md)에 정리했습니다.

## 무엇을 해결하나요?

한국어 행사 정보를 찾아다니는 수고를 줄이기 위해 날짜와 장소, 참가 조건을 영어로 정리합니다. 외국인이 Instagram에서 행사 소식을 읽고 저장할 수 있도록 합니다. 본인 인증이나 예약, 결제 문제까지 해결하는 서비스는 아닙니다.

운영자는 한 줄 요청으로 시작하거나, 행사 URL을 넣고 대화하며 카드 구성을 정할 수 있습니다. 결과 화면에서는 카드뿐 아니라 출처, 제외된 정보, 수정할 표현을 확인합니다.

## 체험 링크와 QR

| 접속 대상 | 주소 | 제공하는 기능 |
|---|---|---|
| 체험 안내 | [안내 페이지](https://juyoung.site/nemo-k-cards/try/) | 예시 요청과 기획 데모 |
| 웹 발표 | [발표 페이지](https://juyoung.site/nemo-k-cards/) | 웹 슬라이드와 최신 공개 PDF 링크 |

<p align="center">
  <a href="https://juyoung.site/nemo-k-cards/try/"><img src="docs/assets/try-qr.png" width="160" height="160" alt="모바일 체험 안내 QR"></a>
</p>

[체험 안내 QR의 SVG 파일](docs/assets/try-qr.svg)을 내려받을 수 있습니다. 안내 페이지에서 이용 가능한 체험을 확인하세요.

## 바로 실행하기

Python 3.12 이상, uv, Node.js 22, pnpm 10이 필요합니다. 카드 이미지는 Playwright Chromium으로 만듭니다.

```bash
git clone https://github.com/juyoungml/nemo-k-cards.git
cd nemo-k-cards/backend
uv sync --frozen
uv run playwright install chromium
DEMO_MODE=fixture PUBLISH_MODE=mock uv run uvicorn app.main:app --port 8000 --reload --reload-dir app
```

다른 터미널에서 저장소 루트로 이동한 뒤 실행합니다.

```bash
cd apps/web
pnpm install --frozen-lockfile
NEXT_PUBLIC_API_URL=http://localhost:8000 pnpm dev
```

[http://localhost:3000](http://localhost:3000)에서 New Job을 열고 요청을 입력하세요. 이 설정에서는 에이전트 응답을 샘플 데이터로 재현하고, 카드 렌더링과 검사는 백엔드에서 실행합니다. 모델이나 Instagram 인증 정보 없이 시작할 수 있고 실제 게시도 하지 않습니다.

`NEXT_PUBLIC_API_URL`을 빼면 백엔드와 별개의 UI mock이 동작합니다. [실행 모드와 배포 안내](docs/GETTING_STARTED.md)를 참고하세요.

UI만 먼저 살펴보려면 `apps/web`에서 `pnpm install --frozen-lockfile` 후 `pnpm dev`를 실행하면 됩니다. 이때 `NEXT_PUBLIC_API_URL`은 지정하지 않습니다. 샘플 상태는 서버 재시작 시 초기화됩니다.

## 검수와 게시 권한

![카드와 검수 근거를 함께 보여주는 관리자 화면](docs/assets/screenshots/review-mock.jpg)

위 화면은 시연용 데이터입니다. 운영자는 검수 경고를 확인한 뒤 게시 여부를 결정합니다. 대부분의 경고는 게시를 자동으로 막지 않지만, 자격증명으로 의심되는 내용과 서버 설정을 넘어서는 게시 모드 요청은 승인 API에서 차단합니다.

- OpenShell은 에이전트의 실행 프로그램, 대상 호스트, 허용 요청을 검사합니다.
- Instagram API는 에이전트에게 읽기 전용으로 허용합니다. 게시 토큰은 에이전트에게 전달하지 않습니다.
- 공통 테스트 폴더는 `/hackathon/input`을 읽기 전용, `/hackathon/output`을 쓰기 가능으로 둡니다. `restricted`와 `secrets`는 정책에 없어서 목록도 내용도 볼 수 없습니다([상세](docs/BACKEND.md#03-handling-the-common-test-folders-hackathon)).
- 실제 게시는 운영자의 승인 뒤 백엔드가 수행합니다.
- 로컬 Claude 실행은 개발용이며 OpenShell 격리를 제공하지 않습니다.

[정책 파일](policies/openshell/agent-policy.yaml) · [실행부](backend/app/pipeline/agent_runner.py) · [정책 검증 스크립트](scripts/openshell_probe.sh)

![실제 Admin의 정책 로그 화면](docs/assets/screenshots/policy-log.jpg)

실제 Admin에 표시된 테스트 요청의 거부 기록입니다. 해당 요청이 거부됐음을 보여주며, 다른 공격까지 모두 차단한다는 뜻은 아닙니다. 저장소의 [probe 로그](backend/tests/data/openshell-probe.txt)와 [파서 테스트](backend/tests/test_policy_log.py)도 함께 확인할 수 있습니다.

## 실행 모드

| 목적 | 설정 | 게시 동작 |
|---|---|---|
| 샘플 파이프라인 | `DEMO_MODE=fixture` | `PUBLISH_MODE=mock`으로 게시 모의 실행 |
| 실제 에이전트 개발 | `DEMO_MODE=live`, `AGENT_RUNNER=local` | 게시 모드는 별도 설정 |
| OpenShell에서 실행 | `DEMO_MODE=live`, `AGENT_RUNNER=openshell` | 게시 모드는 별도 설정 |
| 실제 Instagram 게시 | `PUBLISH_MODE=graph` | 계정 인증 정보와 운영자 승인 필요 |

`dryrun`은 이미지 업로드와 Instagram 컨테이너 생성까지 수행하지만 최종 게시하지는 않습니다. 외부에서 접근할 수 있는 백엔드에는 `ADMIN_TOKEN`과 허용할 `CORS_ORIGINS`를 설정하세요. 현장 접근 코드와 임시 터널 주소는 공개 저장소에 넣지 않습니다.

## 개발과 기여

```bash
cd backend
uv run pytest -q
uv run python -m app.renderer.preview
```

위 테스트는 실제 OpenShell 샌드박스를 실행하지 않습니다. 실행 환경의 권한 제한은 별도로 설정한 환경에서 `scripts/openshell_probe.sh`로 확인합니다.

행사 출처를 추가하거나 날짜가 충돌하는 테스트 자료를 만드는 기여를 환영합니다. 정책 검증을 재현하기 쉽게 만들거나, 카드 가독성과 설치 과정을 개선하는 작업도 도움이 됩니다. [기여 안내](CONTRIBUTING.md)와 [다음 과제](docs/ROADMAP.md)를 확인해 주세요. 유용했다면 스타로 프로젝트를 알려주시고, 문제가 있다면 재현 방법과 함께 이슈를 남겨주세요.

## 문서와 라이선스

[시작하기](docs/GETTING_STARTED.md) · [제품 명세](docs/SPEC.md) · [백엔드 설계](docs/BACKEND.md) · [디자인](DESIGN.md) · [로고](docs/assets/brand/README.md) · [보안 제보](SECURITY.md) · [공개 검수 기록](docs/PUBLIC_REVIEW.md)

프로젝트 코드와 직접 제작한 브랜드 자산은 [MIT 라이선스](LICENSE)로 제공합니다. 폰트와 사진, 행사 포스터 등 외부 자료에는 각 자료의 이용 조건이 적용됩니다. [미디어 출처](docs/SHOWCASE.md#media-attribution)를 함께 확인하세요.
