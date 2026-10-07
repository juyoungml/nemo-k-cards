export const presets = [
  { label: "This weekend in Seoul", prompt: "이번 주말 서울에서 외국인이 즐길 만한 행사 5개 조사해서 카드뉴스 만들어줘" },
  { label: "Pop-ups this week", prompt: "이번 주 서울 팝업 5개 조사해서 카드뉴스 만들어줘" },
  { label: "Festival explainer", prompt: "이번 달 축제 하나 골라서 외국인이 알아야 할 맥락까지 설명하는 카드뉴스 만들어줘" },
  { label: "Hidden local events", prompt: "관광객 체크리스트에 없는 로컬 행사 4개 조사해서 카드뉴스 만들어줘" },
];

export const targetOptions = ["First-time visitors", "Families", "Couples", "Students", "Expats"];
export const toneOptions = ["Friendly explainer", "Playful", "Calm & practical"];

export const SCENARIO_LABELS = {
  good: "good — passes review",
  bad: "bad — blocked (REJECTED)",
  fail: "fail — renderer error",
} as const;
