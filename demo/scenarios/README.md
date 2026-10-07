# Demo scenarios (`DEMO_MODE=fixture`)

라이브 웹 상태와 상관없이 데모를 재현하기 위한 고정 데이터를 둡니다 (SPEC §7).

```
<scenario>/briefs.json   # list[EventBrief]
<scenario>/deck.json     # CardDeck
<scenario>/review.json   # ReviewVerdict
```

- `good/`: `READY_FOR_REVIEW`
- `bad/`: `REJECTED` (사기 링크, 민감 표현, 글자 깨짐, PII, 프롬프트 인젝션 페이지)
