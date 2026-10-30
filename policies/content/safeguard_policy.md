# Safeguard policy: what an Organization's account must never post

The safeguard is the hard gate before publishing. A **block** finding means the deck can't be approved, and
the only ways forward are to regenerate it or reject it. A **warn** finding is shown to the operator and does
not stop publishing. Korea-specific dates, phrases and topics are in `sensitive_topics.yaml`, which is part of
this policy.

The audience is foreign visitors and residents of every nationality. Each Deck goes to the Organization's own public
account (`org_profile` says who it is). Whatever the Organization does, the Deck is a guide for foreign readers,
so anything that would embarrass it in front of Korean or international readers counts. The profile never relaxes
this policy.

## Block

| Category | Block when the deck… |
|---|---|
| `hate` | demeans or stereotypes people by nationality, ethnicity, race, religion, gender, sexuality, disability, age or Korean region (e.g. Jeolla/Gyeongsang slurs), or uses slurs, including romanised or punned ones |
| `harassment` | mocks, shames or exposes a real, identifiable private person, or targets a public figure personally |
| `sexual` | contains sexual content or sexualised framing of people or venues, or any sexual reference involving minors |
| `violence` | shows gore, glorifies violence or weapons, or uses violence as a joke |
| `self_harm` | mentions suicide or self-harm, including as slang or a joke (e.g. a bridge as a "jumping spot") |
| `dangerous` | presents illegal drugs, drink-driving, trespassing, unlicensed tours or other illegal or unsafe acts as fun or normal. Cannabis is illegal in Korea for everyone, including foreigners |
| `historical_tragedy` | uses a Korean tragedy, atrocity, disaster or its victims as a hook, pun, sale or party theme, distorts it, or breaks a block rule or the date gate in `sensitive_topics.yaml` |
| `political` | takes a side on parties, candidates, elections, North Korea policy or a live political dispute, or pairs party colours with ballot numbers |
| `territorial_cultural` | uses "Sea of Japan" or "Takeshima", shows rising-sun or radial-ray designs, or claims Korean culture comes from another country (hanbok/hanfu, kimchi/paocai) |
| `scam` | promotes fake giveaways, phishing links, pyramid schemes, or money, health or safety claims that could hurt a reader who relies on them |

Any phrase with `severity: block` in `sensitive_topics.yaml` is always a block. The host also matches those
phrases itself, so the model can't overrule them.

## Warn

- Borderline taste: dark humour, edgy slang, alcohol-centred events (fine to list, but don't hype excess drinking).
- Military imagery in a factual museum or commemoration context.
- Crowd-heavy events, especially Itaewon or Halloween in late October.
- Anything the model is unsure about. Warn and explain, rather than block on a guess.

## Allowed (don't flag)

- Neutral, factual information about memorials, museums, history exhibitions and commemorations, written respectfully.
- Bars, night markets and festivals that serve alcohol, as long as the deck doesn't glorify excess.
- Normal travel safety advice ("keep your passport on you", "the last subway is around midnight").
- Event names that contain a sensitive word in a clearly unrelated sense, when the context makes that obvious.

## How to explain a block

The operator who reads the explanation may not know Korean history. For each finding, quote the exact text,
name the rule, say in one or two sentences why it's harmful to *this* audience, and suggest a concrete fix.
The `summary` explains the decision as a whole in at most three sentences, starting with the most serious problem.
