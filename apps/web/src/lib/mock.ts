// Example data matching the Figma mockups. Replace via lib/api.ts once the backend is live.

import type { Channel, Draft, Job, LogLine, Metric, PipelineStep, PolicyEvent } from "./types";

export const metrics: Metric[] = [
  { label: "Followers", value: "12,480", delta: "+4.2% vs last week" },
  { label: "Reach (7d)", value: "48.2K", delta: "+11.8% vs last week" },
  { label: "Avg. saves / post", value: "312", delta: "+38 vs last week" },
  { label: "Published this week", value: "6", delta: "2 waiting for review" },
];

export const channels: Channel[] = [
  { handle: "@whatsonkorea", platform: "Instagram", followers: 12480, live: true },
  { handle: "@whatsonkorea.jp", platform: "Instagram", followers: 2104, live: false },
  { handle: "What's On Korea", platform: "Threads", followers: 860, live: false },
  { handle: "What's On Korea", platform: "YouTube Shorts", followers: 1320, live: false },
];

const reviewJob: Job = {
  id: "3f9a1c",
  prompt: "이번 주말 서울 팝업 5개 조사해서 카드뉴스 만들어줘",
  status: "READY_FOR_REVIEW",
  created_at: "2026-10-07T10:12:00+09:00",
  briefs: [
    { id: "evt-1", title_en: "Seongsu Stationery Pop-up", title_ko: "성수 문구 팝업", sources: [{ url: "https://english.visitkorea.or.kr/", kind: "official" }] },
    { id: "evt-2", title_en: "Seoul Lantern Walk", title_ko: "청계천", sources: [{ url: "https://culture.seoul.go.kr/", kind: "official" }] },
    { id: "evt-3", title_en: "Modern Hanbok", title_ko: "모던 한복 전시", sources: [{ url: "https://tickets.interpark.com/", kind: "ticketing" }] },
    { id: "evt-x1", title_en: "Free K-pop Ticket Giveaway", title_ko: "무료 티켓", sources: [{ url: "https://visitkorea-tickets.xyz/free", kind: "other" }] },
    { id: "evt-x2", title_en: "Hongdae Character Pop-up", title_ko: "홍대 캐릭터 팝업", sources: [{ url: "https://popply.co.kr/popup-hongdae", kind: "sns" }] },
  ],
  verification: {
    checks: [
      { url: "https://english.visitkorea.or.kr/", status: "ok", http_code: 200 },
      { url: "https://culture.seoul.go.kr/", status: "ok", http_code: 200 },
      { url: "https://tickets.interpark.com/", status: "ok", http_code: 200 },
      { url: "https://visitkorea-tickets.xyz/free", status: "suspicious", reason: "lookalike" },
      { url: "https://popply.co.kr/popup-hongdae", status: "dead", http_code: 404 },
    ],
    excluded_event_ids: ["evt-x1", "evt-x2"],
  },
  deck: {
    job_id: "3f9a1c",
    title: "This weekend in Seoul: 5 pop-ups worth it",
    caption:
      "5 pop-ups foreigners can actually enjoy in Seoul this weekend.\nFree entry, English-friendly, and close to the subway.\nSave this before Friday! Details & links in bio.\n#seoul #seoulpopup #thingstodoinseoul #koreatravel #whatsonkorea",
    slides: [
      { index: 0, layout: "cover", heading: "This weekend in Seoul: 5 pop-ups worth it", body: "Verified dates, venues & tips for foreigners" },
      { index: 1, layout: "event", event_id: "evt-1", heading: "Stationery Pop-up (성수 문구 팝업)", body: "📅 Oct 9 – 19\n📍 Seongsu Stn. (Line 2) Exit 3\n💸 Free · 🎟 Walk-in" },
      { index: 2, layout: "event", event_id: "evt-2", heading: "Seoul Lantern Walk (청계천)", body: "📅 Oct 10 – 12, best after 7pm\n📍 Gwanghwamun Stn. (Line 5) Exit 5\n💸 Free" },
      { index: 3, layout: "event", event_id: "evt-3", heading: "Modern Hanbok (모던 한복 전시)", body: "📅 Until Nov 30\n📍 Anguk Stn. (Line 3) Exit 1\n💸 ₩10,000" },
      { index: 4, layout: "event", heading: "Must-visit: Character Café", body: "📅 Oct 9 – 12\n📍 Hongdae Stn. Exit 9" },
      { index: 5, layout: "tips", heading: "Before you go", body: "• T-money works on subway & bus\n• Weekday mornings = short lines" },
      { index: 6, layout: "cta", heading: "Save this for the weekend", body: "Follow @whatsonkorea for verified events every week" },
    ],
  },
  issues: [
    { severity: "warn", category: "tone", slide_index: 4, message: "\"must-visit\" is clickbait-y; consider \"worth a look\"." },
  ],
};

export const jobs: Job[] = [
  reviewJob,
  { id: "a17c02", prompt: "A festival looks fun — what to know first", status: "READY_FOR_REVIEW", created_at: "2026-10-07T09:40:00+09:00", briefs: [], issues: [], deck: { job_id: "a17c02", title: "A festival looks fun — what to know first", caption: "", slides: Array.from({ length: 8 }, (_, i) => ({ index: i, layout: i === 0 ? "cover" : "event", heading: "", body: "" })) } },
  { id: "e04b91", prompt: "Seoul pop-ups you can't miss", status: "REJECTED", created_at: "2026-10-06T16:20:00+09:00", briefs: [], issues: [{ severity: "block", category: "sensitive", slide_index: 1, message: "'Tank Day' on May 18 evokes the 5·18 Gwangju Uprising." }], deck: { job_id: "e04b91", title: "Seoul pop-ups you can't miss", caption: "", slides: Array.from({ length: 6 }, (_, i) => ({ index: i, layout: "event", heading: "", body: "" })) } },
  { id: "c3d810", prompt: "3 exhibitions worth visiting this week", status: "PUBLISHED", created_at: "2026-10-05T11:00:00+09:00", briefs: [], issues: [], deck: { job_id: "c3d810", title: "3 exhibitions worth visiting this week", caption: "", slides: Array.from({ length: 7 }, (_, i) => ({ index: i, layout: "event", heading: "", body: "" })) } },
  { id: "9be7f3", prompt: "Hidden local events beyond the checklist", status: "PUBLISHED", created_at: "2026-10-04T11:00:00+09:00", briefs: [], issues: [], deck: { job_id: "9be7f3", title: "Hidden local events beyond the checklist", caption: "", slides: Array.from({ length: 6 }, (_, i) => ({ index: i, layout: "event", heading: "", body: "" })) } },
];

export const runningJob = { id: "3f9a1c", prompt: reviewJob.prompt };

export const pipeline: PipelineStep[] = [
  { name: "Research", where: "researcher · sandbox", state: "done", note: "5 events · 11 sources" },
  { name: "Verify links", where: "host", state: "done", note: "1 dead · 1 lookalike → 2 excluded" },
  { name: "Outline & copy", where: "copywriter · sandbox", state: "done", note: "7 slides" },
  { name: "Render", where: "host · Playwright", state: "running", note: "slide 4 / 7" },
  { name: "Visual QA", where: "host", state: "pending" },
  { name: "Final review", where: "reviewer · sandbox", state: "pending" },
  { name: "Human publish", where: "you", state: "pending", note: "Approve in Review" },
];

export const logLines: LogLine[] = [
  { time: "10:12:03", stage: "sandbox", message: "created whatsonkorea-agent (policy: agent-policy.yaml)" },
  { time: "10:12:05", stage: "research", message: "TourAPI searchFestival2 → 14 candidates" },
  { time: "10:12:19", stage: "research", message: "WebFetch culture.seoul.go.kr … ok" },
  { time: "10:12:31", stage: "research", message: "5 EventBrief validated (schema ok)" },
  { time: "10:12:32", stage: "verify", message: "https://visitkorea-tickets.xyz/free → SUSPICIOUS (lookalike)", level: "warn" },
  { time: "10:12:33", stage: "verify", message: "https://popply.co.kr/popup-hongdae → DEAD (404)", level: "warn" },
  { time: "10:12:40", stage: "copy", message: "CardDeck 7 slides · caption 9 hashtags" },
  { time: "10:12:44", stage: "render", message: "slide-00.png … slide-03.png" },
];

export const presets = [
  { label: "This weekend in Seoul", prompt: "이번 주말 서울에서 외국인이 즐길 만한 행사 5개 조사해서 카드뉴스 만들어줘" },
  { label: "Pop-ups this week", prompt: "이번 주 서울 팝업 5개 조사해서 카드뉴스 만들어줘" },
  { label: "Festival explainer", prompt: "이번 달 축제 하나 골라서 외국인이 알아야 할 맥락까지 설명하는 카드뉴스 만들어줘" },
  { label: "Hidden local events", prompt: "관광객 체크리스트에 없는 로컬 행사 4개 조사해서 카드뉴스 만들어줘" },
];

export const policyStats: Metric[] = [
  { label: "Denied (24h)", value: "7", delta: "2 publish / delete attempts" },
  { label: "Audited (24h)", value: "41", delta: "event_pages in audit mode" },
  { label: "Sandboxes run", value: "18", delta: "all discarded (--no-keep)" },
  { label: "Policy version", value: "v3", delta: "prover: no risky diff" },
];

export const policyEvents: PolicyEvent[] = [
  { time: "10:14:52", sandbox: "agent-3f9a1c", binary: "/usr/local/bin/claude", host: "graph.facebook.com", request: "DELETE /v21.0/17952…/  (injected by event page)", result: "policy_denied" },
  { time: "10:14:51", sandbox: "agent-3f9a1c", binary: "/usr/bin/curl", host: "pastebin.com", request: "POST /api/api_post.php  (exfiltration attempt)", result: "policy_denied" },
  { time: "10:14:40", sandbox: "agent-3f9a1c", binary: "/usr/local/bin/claude", host: "culture.seoul.go.kr", request: "GET /event/night-market", result: "audit" },
  { time: "10:12:19", sandbox: "agent-3f9a1c", binary: "/usr/local/bin/claude", host: "culture.seoul.go.kr", request: "GET /", result: "audit" },
  { time: "10:12:05", sandbox: "agent-3f9a1c", binary: "/usr/bin/curl", host: "apis.data.go.kr", request: "GET /B551011/EngService2/searchFestival2", result: "allowed" },
  { time: "10:12:03", sandbox: "agent-3f9a1c", binary: "/usr/local/bin/claude", host: "api.anthropic.com", request: "POST /v1/messages  (provider key injected)", result: "allowed" },
  { time: "09:41:10", sandbox: "agent-77b0e2", binary: "/usr/local/bin/claude", host: "graph.facebook.com", request: "POST /v21.0/178414…/media", result: "policy_denied" },
  { time: "09:40:58", sandbox: "agent-77b0e2", binary: "/usr/bin/python3", host: "—", request: "write /sandbox/agent/CLAUDE.md  (read-only path)", result: "fs_denied" },
];

export const draft: Draft = {
  id: "d-7k2m",
  messages: [
    {
      role: "user",
      text: "망원 야시장 홍보하고 싶어요. 외국인 친구들이 망원은 잘 모르는데, 로컬 분위기 + 먹거리 위주로 알리고 싶어요.",
      urls: ["https://culture.seoul.go.kr/event/mangwon-night-market"],
    },
    {
      role: "agent",
      text: "페이지 확인했어요 ✓ 10/17–10/19, 망원한강공원, 입장 무료, 현금·카드 모두 가능. 외국인에게 먹힐 앵글 3개를 오른쪽 보드에 올려 뒀어요.",
    },
    { role: "user", text: "B로 가고, 처음 한국 온 사람 기준으로 써주세요. 줄 서는 팁도 넣어줘요." },
    {
      role: "agent",
      text: "좋아요. 타깃을 ‘First-time visitors’로, 슬라이드 4에 줄 서기·결제 팁을 넣었어요. 메뉴 가격은 공식 페이지에 없어서 ‘가격대 미확인’으로 표시해 둘게요.",
    },
  ],
  facts: [
    { key: "event", value: "Mangwon Night Market (망원 야시장)", verified: true, source_url: "https://culture.seoul.go.kr/event/mangwon-night-market" },
    { key: "dates", value: "Oct 17–19 · 5–10 pm", verified: true, source_url: "https://culture.seoul.go.kr/event/mangwon-night-market" },
    { key: "venue", value: "Mangwon Hangang Park", verified: true, source_url: "https://culture.seoul.go.kr/event/mangwon-night-market" },
    { key: "price", value: "Free entry · menu ₩ unverified", verified: false },
  ],
  angles: [
    { id: "a", title: "The night market locals keep to themselves", hook: "Discovery hook · local vibe" },
    { id: "b", title: "Eat like a local by the Han River", hook: "Food-first · first-timers · what to order" },
    { id: "c", title: "A cheap date night in Mangwon", hook: "Couples · budget · sunset timing" },
  ],
  selected_angle_id: "b",
  targets: ["First-time visitors"],
  tones: ["Friendly explainer"],
  outline: [
    { layout: "cover", heading: "Eat like a local by the Han River" },
    { layout: "event", heading: "Mangwon Night Market (망원 야시장) — dates, venue, how to get there" },
    { layout: "event", heading: "What to order: 5 stalls locals line up for" },
    { layout: "tips", heading: "How lines & payment work (cash / card / T-money)", updated_from_chat: true },
    { layout: "tips", heading: "Why Mangwon? A 30-sec neighborhood intro" },
    { layout: "map", heading: "Getting there: Mangwon Stn. Exit 1 → 10 min walk" },
    { layout: "cta", heading: "Save this for Friday · Follow @whatsonkorea" },
  ],
};

export const targetOptions = ["First-time visitors", "Families", "Couples", "Students", "Expats"];
export const toneOptions = ["Friendly explainer", "Playful", "Calm & practical"];
