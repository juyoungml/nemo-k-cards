// Static seed data for the mock backend (channels, baseline policy log, sample Brainstorm draft).

import type { Channel, Draft, PolicyEvent } from "@/lib/types";

export const seedChannels: Channel[] = [
  { handle: "@whatsonkorea", platform: "Instagram", followers: 12480, live: true },
  { handle: "@whatsonkorea.jp", platform: "Instagram", followers: 2104, live: false },
  { handle: "What's On Korea", platform: "Threads", followers: 860, live: false },
  { handle: "What's On Korea", platform: "YouTube Shorts", followers: 1320, live: false },
];

export const seedPolicyEvents: PolicyEvent[] = [
  { time: "10:12:05", sandbox: "agent-3f9a1c", binary: "/usr/bin/curl", host: "apis.data.go.kr", request: "GET /B551011/EngService2/searchFestival2", result: "allowed" },
  { time: "10:12:03", sandbox: "agent-3f9a1c", binary: "/usr/local/bin/claude", host: "api.anthropic.com", request: "POST /v1/messages  (provider key injected)", result: "allowed" },
  { time: "09:41:10", sandbox: "agent-77b0e2", binary: "/usr/local/bin/claude", host: "graph.facebook.com", request: "POST /v21.0/178414…/media", result: "policy_denied" },
  { time: "09:40:58", sandbox: "agent-77b0e2", binary: "/usr/bin/python3", host: "—", request: "write /sandbox/agent/CLAUDE.md  (read-only path)", result: "fs_denied" },
];

const SRC = "https://culture.seoul.go.kr/event/mangwon-night-market";

export const seedDraft: Draft = {
  id: "d-sample",
  messages: [
    { role: "user", text: "망원 야시장 홍보하고 싶어요. 외국인 친구들이 망원은 잘 모르는데, 로컬 분위기 + 먹거리 위주로 알리고 싶어요.", urls: [SRC] },
    { role: "agent", text: "페이지 확인했어요 ✓ 10/17–10/19, 망원한강공원, 입장 무료, 현금·카드 모두 가능. 외국인에게 먹힐 앵글 3개를 오른쪽 보드에 올려 뒀어요." },
    { role: "user", text: "B로 가고, 처음 한국 온 사람 기준으로 써주세요. 줄 서는 팁도 넣어줘요." },
    { role: "agent", text: "좋아요. 타깃을 ‘First-time visitors’로, 슬라이드 4에 줄 서기·결제 팁을 넣었어요. 메뉴 가격은 공식 페이지에 없어서 ‘가격대 미확인’으로 표시해 둘게요." },
  ],
  facts: [
    { key: "event", value: "Mangwon Night Market (망원 야시장)", verified: true, source_url: SRC },
    { key: "dates", value: "Oct 17–19 · 5–10 pm", verified: true, source_url: SRC },
    { key: "venue", value: "Mangwon Hangang Park", verified: true, source_url: SRC },
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
