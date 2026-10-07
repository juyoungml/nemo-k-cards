# Cards and evidence

## Output examples

The October 7, 2026 project publishing run recorded these post URLs:

| Output | Public post |
|---|---|
| A slower side of Seoul, including Seoul Forest | [Instagram carousel](https://www.instagram.com/p/DeL2WwdGQP5/) |
| Creative X Seongsu | [Instagram carousel](https://www.instagram.com/p/DeL2e1vmXz4/) |

The README uses the exported cards from that run. These are editorial examples of the target output, not evidence that every image was created by an unattended pipeline. Event details are dated and should be rechecked before travel.

## Review interface

[The review screenshot](assets/screenshots/review-mock.jpg) shows source URLs, excluded links, a wording warning and human approval. It contains UI mock data. It demonstrates the interface, not factual verification accuracy.

## Policy evidence

[The Policy Log screenshot](assets/screenshots/policy-log.jpg) was captured from the running Admin on October 7, 2026. It displays file-write and external-request denials alongside allowed reads. Displayed requests include controlled drill sandboxes, so a denial is not evidence that the model independently followed a malicious instruction.

The repository separately includes a [real OpenShell 0.1.2 probe capture](../backend/tests/data/openshell-probe.txt), documented by [its tests](../backend/tests/test_policy_log.py). The capture maps requests to results, including:

| Probe | Recorded result |
|---|---|
| Instagram API GET | Allowed by policy; this does not imply a successful authenticated API response |
| Instagram API POST | `policy_denied` |
| Facebook Graph DELETE | `policy_denied` |
| Connection to pastebin.com | `policy_denied` |
| Event-page curl connection outside its executable allowlist | `policy_denied` |

This is a small set of enforcement observations. No general security score, attack success rate or production-readiness claim is derived from it. Run [the probe script](../scripts/openshell_probe.sh) to evaluate your own environment.

## Presentation

[Public presentation PDF](../presentation/Nemo-K-Cards-Presentation.pdf) contains the cover and nine content pages exported from the final Figma deck. The last on-site page, which contained an active access code and temporary Admin QR, is excluded from the public copy. Some presentation screenshots document earlier sample-data states; the [current source](../backend/app/pipeline/agent_runner.py) and [setup guide](GETTING_STARTED.md) describe implemented execution modes.

## Media attribution

The MIT license covers project code and original brand assets, not the following third-party media.

| File | Source and terms |
|---|---|
| `cards/slow-seoul.jpg` photograph | [Ryu Kim / Unsplash](https://unsplash.com/photos/a-man-and-a-child-are-walking-down-a-path-S7t46jP_GWM), [Unsplash License](https://unsplash.com/license). Archive photo, not a dated event photograph. |
| `cards/seoul-forest.jpg` photograph | [Yonghyun Lee / Unsplash](https://unsplash.com/photos/green-grass-field-with-trees-and-high-rise-buildings-in-distance-JQC6_cyrGhI), [Unsplash License](https://unsplash.com/license). Archive photo. |
| `cards/seongsu.jpg` event artwork | Organizer promotional material, referenced by the [Seoul Culture Portal listing](https://culture.seoul.go.kr/culture/culture/cultureEvent/view.do?cultcode=159252&menuNo=200010). Included to show the project's event-card example. Independent reuse permission is not established by this repository. |
| Admin screenshots | Captures of this project's UI. Review image uses mock data; policy image shows the running Admin's records. |
| Brand SVG/PNG files | Original project identity assets, MIT. They are not official NVIDIA or Instagram logos. |
| Bundled fonts | See license files under [renderer/fonts](../backend/app/renderer/fonts/) and the frontend font directory. |

The public slide deck also contains attributed Reddit excerpts and official event screenshots. Their original links and interpretation limits are preserved in the [presentation sources](../presentation/sources.html) and [manifest](../presentation/manifest.json).
