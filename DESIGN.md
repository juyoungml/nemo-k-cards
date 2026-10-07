# What's On Korea · Design System

Approved 2026-10-07. Reference page: https://claude.ai/artifact/AUcknG2V77ewNMXrw2H152

**North star:** "this feels like a Seoul magazine." Every carousel is a weekend issue for tourists. It's photo-led, confident type, generous white, and minimal decoration. Event slides come in two approved styles: **Photo overlay** (Kinfolk-style, when there's a usable photo) and **Poster** (when there isn't).

Applies to the Instagram cards (`backend/app/renderer/`) and the Admin (`apps/web/`).

## Color

| Token | Hex | Use |
|---|---|---|
| Ink | `#111418` | Text, primary buttons, dark covers |
| Paper | `#FFFFFF` | Card and UI surface |
| Mist | `#F2F3F5` | Secondary surfaces, muted fills |
| Muted | `#5E6573` | Secondary text, labels |
| Seoul Red | `#E4002B` | The one accent: dates, numerals, labels, blocking issues. `#FF5C76` on dark |
| Taegeuk red / blue | `#CD2E3A` / `#0047A0` | **Logo mark only** |

### Category colors (Seoul Metro lines)

Categories wear their subway-line color, as a round line badge (`(8) POP-UP`) or as the full poster background.

| Category | Line | Hex | Poster text / title |
|---|---|---|---|
| Pop-up | 8 | `#E6186C` | Ink / white |
| Exhibition | 1 | `#0052A4` | White / white |
| Festival | 3 | `#EF7C1C` | Ink / white |
| Performance | 5 | `#996CAC` | White / white |
| Experience · food | 2 | `#00A84D` | White / white |
| Other | 9 | `#BDB092` | Ink / ink |

Source of truth: `CATEGORY` in `backend/app/renderer/html.py`, plus `--color-cat-*` in `apps/web/src/app/globals.css`.

## Type

- **Display:** Cabinet Grotesk 800 (Fontshare, ITF Free Font License). Used for English headlines, numerals, the wordmark, and Admin page titles (`h1`).
- **Korean, body, UI:** Pretendard 400/700/900 (SIL OFL). Hangul display uses Pretendard 900.
- **Photo overlay serif:** Cormorant Garamond (OFL) for headlines, numerals and values, with the last word in italic. Gowun Batang (OFL) for Korean on these slides only.
- Card scale (px at 1080w): label 30 (tracked .14em, uppercase) · body 36–40 · event title 76–108 · cover title 104–132 · numerals 210–330.
- **Floor: 30px on cards.** Visual QA warns below it, and FIT_JS shrinks blocks to it before giving up.
- Fonts are bundled. The renderer loads them from `renderer/fonts/` and the Admin uses `next/font/local` with `src/app/fonts/`. No network at render time.

## Card styles (1080×1350, 80px margins, 8px spacing)

| Slide | Style |
|---|---|
| Cover | Ink, or a licensed photo under a scrim. "Weekend Issue No. NN" (ISO week of the posting weekend), a huge translucent Hangul place name (성수 / 서울) behind the headline, posting-weekend date in red |
| Event, with usable photo | **Photo overlay (Kinfolk)**: full-bleed photo graded muted and warm with light grain, soft dark gradient under the text. Italic serif "No. 01", tiny tracked category and area, a 108px thin serif headline with the last word in italic, Korean name in Gowun Batang, and a 2×2 WHEN / WHERE / PRICE / ENTRY grid under a hairline. No why-go line (the caption carries it). Credit above the footer |
| Event, no photo | **Poster**: the category color fills the card, with a giant numeral, a big headline, and the info grid pinned to the bottom |
| Tips | White, red Cabinet numerals, hairline rules |
| Map | White, metro-colored route line, ink taxi box with the Korean address large |
| CTA | Ink back cover, "Links checked" stamp, sources |

Chrome on every slide: taegeuk mark + `WHAT'S ON KOREA` (top left), `01 / 07` (top right), `@whatsonkorea` and `Swipe →` (bottom).

Photos are used only when the license allows overlay (`ImageAsset.allow_overlay`), and the credit is always shown. There are never placeholder photos. Without a photo, the poster style is the design.

**Photo sourcing order** (per event):
1. Official or public-license photos: KTO photo gallery (관광사진갤러리), 공공누리 Type 1, city or festival press photos. Credit plus source link in the brief.
2. Official promo image when reuse is allowed, with credit and link.
3. AI-generated, only for mood: close-ups with no recognisable landmark, never a fake wide shot of a real named place. Always labelled `Image · AI-generated`.
4. Nothing: use the Poster style.

Never use other creators' photos.

The Magazine layout (photo top, white info panel) is parked, not approved. It's in git history (`58c16e6`).

## Admin

- Pretendard everywhere; Cabinet Grotesk only for `h1` and the sidebar wordmark.
- Primary button = Ink. Red = destructive, blocking issues, and active nav tint. Status colors stay semantic (success green, warning amber, info blue).
- Canvas `#F6F7F9`, borders `#E2E5EA`, radius 12px.

## Motion

Minimal and functional: progress line, toast slide-in, 150–500ms ease-out. Respect `prefers-reduced-motion`.

## Don'ts

No emoji on cards, no purple gradients, no cartoon illustration, no centered-everything layouts, no taegeuk colors outside the mark, no other creators' photos.
