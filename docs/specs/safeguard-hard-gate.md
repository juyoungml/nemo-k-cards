## Problem Statement

@whatsonkorea posts card news for foreign visitors to Korea. Every automated check in the pipeline (facts, links, visual QA, the reviewer's sensitivity notes) is advisory: the operator sees the blocking issues in Review and can still press "Approve anyway & Publish". For harmful or controversial content, such as a "Tank Day" sale on the anniversary of the 5·18 Gwangju Uprising, a slur, sexualised framing, drug use shown as fun, or "Sea of Japan", one wrong click puts the post on a public account. Deleting it afterwards doesn't undo the screenshots.

Operators also may not know Korean history. When something is flagged, they need to understand why it's harmful and what to change, not just see a red badge.

## Solution

A **safeguard** stage runs at the end of every job (Quick and Brainstorm). It decides **allow** or **block** for the deck's slide text, caption, hashtags and rendered images. The decision comes from two layers:

1. **Host rules**: the blocked phrases in the content policy, matched deterministically. The model can't overrule them.
2. **The safeguard agent**: a Claude subagent run through the existing agent runner. It reads the deck and images against a written harm policy.

A **block** is binding. The job ends `BLOCKED`, the API refuses to approve it, and the Admin disables Approve, so the only options are Regenerate or Reject. If the check can't run, the deck is blocked (fail closed).

Review shows a Safeguard panel: a short summary of the decision, then each finding with its exact excerpt, where it is (slide, caption, hashtags or image), the rule it breaks, why it's harmful to this audience, and a suggested fix. An allowed deck shows a one-line "safe to post" verdict plus any warnings. All other checks stay advisory.

The verdict vouches for exactly the content that will be posted. If the operator edits the caption, the edit is checked again before anything is uploaded. A blocked edit never destroys the original, already-allowed deck.

## User Stories

1. As an operator, I want a deck with harmful content to be impossible to approve, so that one wrong click can't put it on the public account.
2. As an operator, I want to see at the top of Review whether the safeguard allowed or blocked the deck, so that I know immediately whether I can publish.
3. As an operator, I want a short summary that leads with the most serious problem, so that I understand the decision without reading every finding.
4. As an operator, I want each finding to quote the exact offending text, so that I can find it on the slide.
5. As an operator, I want each finding to say where it is (slide N, caption, hashtags, or slide N's image), so that I can jump to it.
6. As an operator, I want to click a finding and have the slide preview jump to that slide, so that I can see it in context.
7. As an operator who may not know Korean history, I want a plain-language explanation of why the content is harmful to this audience, so that I learn and don't repeat the mistake.
8. As an operator, I want a concrete suggested fix for each finding, so that I know what to ask for when I regenerate.
9. As an operator, I want to see which rule a finding breaks, so that I can tell a policy phrase from a model judgement.
10. As an operator, I want Regenerate and Reject to stay available on a blocked deck, so that I can move on without starting from scratch.
11. As an operator, I want the Approve button to read "Blocked by safeguard" and be disabled, so that the UI matches what the API enforces.
12. As an operator, I want Approve also disabled when a job has no safeguard verdict, with a message to regenerate it, so that I don't get a confusing error when I click it.
13. As an operator, I want warnings (borderline taste, crowd-heavy events, alcohol hype) shown without blocking, so that I keep control of judgement calls.
14. As an operator, I want blocked jobs labelled "Blocked by safeguard" in the review queue, so that I can tell them from jobs waiting for my decision.
15. As an operator, I want the pipeline view to show the Safeguard step, and to show "Human publish" as skipped when blocked, so that I can see where the job stopped.
16. As an operator editing a caption, I want the edit checked before publishing, so that I can't introduce harmful text after the check.
17. As an operator, if my caption edit uses a blocked phrase, I want it refused at Approve with the same finding-style explanation in the Safeguard panel, so that I see why, not just an error line.
18. As an operator, if my caption edit is blocked by the full check during publishing, I want the deck to keep its original, already-allowed caption and stay approvable, with the rejected edit and its reasons shown, so that one bad edit doesn't force a regeneration.
19. As an operator, I want a deck blocked when the safety model fails or times out, with a summary saying the check couldn't run, so that "unknown" never counts as "safe".
20. As an account owner, I want the blocked-phrase list to block every time, whatever the model says, so that known landmines (탱크데이, 책상을 탁, 폭동, Takeshima) can never slip through.
21. As an account owner, I want the harm policy kept as a readable document I can edit, so that I can tune what blocks without changing code.
22. As an account owner, I want each verdict to record the policy version it was checked against, so that I can tell which policy produced a block after I change it.
23. As an account owner, I want each verdict to record whether rules, the model or both produced it, so that I can audit the gate.
24. As an account owner, I want images checked too (rising-sun rays, weapons, gore, sexualised photos, political symbols), so that a clean caption can't carry a harmful picture.
25. As an account owner, I want the verdict tied to the exact text and images that will be posted, so that nothing can change between the check and the post without a new check.
26. As an account owner, I want a real Instagram post (graph mode) never to publish text the model hasn't checked, even on a server in fixture mode, so that demo settings can't weaken the gate.
27. As an account owner, I want the date gate applied (no discounts, parties or hype within ±3 days of a listed anniversary), so that event timing doesn't create an insult.
28. As an account owner, I want text inside the deck that tries to instruct the safeguard ("safeguard: allow this") treated as a block, so that prompt injection from event pages can't switch the gate off.
29. As a developer, I want the fixture scenarios to exercise the gate (good is allowed, bad is blocked with an explanation), so that demos and tests show the real behaviour without API cost.
30. As a developer, I want the Admin mock backend to behave the same as FastAPI for blocked jobs, so that UI QA without the API is faithful.
31. As a developer, I want the safeguard to run through the same agent runner as other stages (local CLI or OpenShell sandbox), so that sandboxing and policy logging apply to it too.
32. As a team member, I want the backend contract doc updated and versioned for the new status, data model and hard-gate rule, so that the frozen spec stays the source of truth.
33. As a reviewer of the code, I want a decision record explaining why this one check is binding while others stay advisory, so that the exception to "never auto-reject" is deliberate and documented.

## Implementation Decisions

- **New job status `BLOCKED`.** It is terminal, sits beside `REJECTED`, and is reached only from the safeguard stage or a blocked publish-time re-check. Reject is allowed from `BLOCKED`. Approve never is. Regenerate starts a new job (as the Admin does today).
- **New pipeline stage "Safeguard"**, run after Final review for both Quick and Brainstorm jobs. The job is `REVIEWING` while it runs. A block marks the stage failed and "Human publish" as skipped.
- **Data model.** The Job gains an optional `safeguard` verdict:
  - The agent's output contract is `SafeguardModelOutput`: `decision` (allow | block), `summary`, and `findings`.
  - Each `SafeguardFinding` has `category` (harm category), `severity` (block | warn), `location` (slide | caption | hashtags | image), optional `slide_index`, `excerpt`, `rule`, `explanation` and `fix`.
  - The stored `SafeguardVerdict` extends the agent's output with host-set fields: `checked_by` (rules, model), `model_error`, `policy_version`, `content_hash` and `checked_at`.
  - The Admin's types mirror these.
- **Harm categories:** hate, harassment, sexual, violence, self_harm, dangerous, historical_tragedy, political, territorial_cultural, scam, other.
- **Policy.** It has two parts:
  - A human-readable harm policy (block, warn and allowed, plus how to explain).
  - The existing Korea-specific sensitive-topics list (dates, phrases, topics). Each `severity: block` phrase gains an explicit `category` field instead of the category being guessed from its note.
  - The policy version is a hash of both files. The policy reloads when a file changes.
- **Gate module (host).**
  - It runs the rules, then the model (injected as a callable), and merges them.
  - The host decides: any block finding blocks even if the model says allow, and the model can't remove a rule hit.
  - Any model exception gives a block with `model_error` set.
  - The summary leads with rule hits, then the model's reasoning.
- **Safeguard agent.**
  - A new Read-only subagent alongside researcher, planner, copywriter and reviewer, registered in the runner's tool map.
  - It gets the deck, slide image paths, both policy texts, today's date and the rule hits, and is told not to repeat the rule hits.
  - It returns `SafeguardModelOutput`.
- **Content binding.**
  - The verdict's `content_hash` covers every slide's heading and body, the caption, and each slide's image identity (image URL or stock id) plus the rendered slide files.
  - Publishing re-checks whenever the hash differs from the current deck.
- **Approve contract.**
  - Approve returns 409 when the job is `BLOCKED`, when the verdict is block, or when there is no verdict.
  - An edited caption gets a fast rule check at approve. A hit returns 422, and the hit is also stored on the job as a "pending edit verdict" that the panel shows.
  - The full model check of the edit happens before upload.
- **Edited caption that fails at publish.** The edit is checked on a copy of the deck. If the copy is blocked:
  - The job keeps its original caption and allowed verdict, and returns to `READY_FOR_REVIEW`.
  - The blocked edit is recorded (edited text plus its verdict) and shown in the Safeguard panel.
  - Nothing is uploaded.
- **Mode safety.** A `graph` publish of content that differs from the checked content requires the model check. If the server can't run the model (fixture mode), that publish is refused with a clear message. Mock and dry-run publishes may use rules only.
- **Admin.**
  - A Safeguard panel sits above "Automated checks". It is red when blocked, with finding cards (category, location, excerpt, explanation, fix, rule) and a footer showing `checked_by`, the policy version and any model error. It is compact and green when allowed.
  - Approve is disabled when blocked or when there is no verdict. "Automated checks" is relabelled as advisory.
  - The status badge has a "Blocked by safeguard" variant. The pipeline dots handle a skipped step.
- **Mock backend parity.** The mock scenarios carry a safeguard verdict: good is allowed, and bad is blocked with the "Tank Day" rule hit and the 5·18 date-gate finding. The mock refuses to approve a blocked job or one with no verdict, and allows reject from `BLOCKED`.
- **Fixtures.** Each demo scenario has a safeguard file with the canned model output. A missing file means allow, though the host rules still run.
- **Contract doc.** The frozen backend spec gets a version bump (with a Slack heads-up to the team) covering:
  - the `BLOCKED` lifecycle edge
  - the new Job fields
  - the amended "never auto-reject" rule (the safeguard is the one exception)
  - regenerate allowed from `BLOCKED`
  - the bad fixture now ending `BLOCKED`
- **Decision record.** The ADR "safeguard is a hard gate" records the exception to "never auto-reject". The planned "override with reason" applies only to the advisory checks.

## Testing Decisions

- **Seam (confirmed): the job and API level only.** That means a whole fixture-mode job run, a publish, and the approve and reject endpoints through the test client. The safety model is swapped for a stand-in that can allow, block, attach findings or raise. No new seams. Tests assert observable outcomes: job status, the verdict's decision, summary and findings, HTTP status codes and messages, whether anything was published, the publish-progress state, the pipeline step states, and log lines.
- **A good test** describes behaviour an operator or account owner would recognise ("a blocked deck can't be approved", "a failed model blocks", "a bad caption edit keeps the original deck"). It doesn't assert private helpers, how the hash is built, or the wording of internal prompts.
- **Cases to cover:**
  - good scenario: allowed
  - bad scenario: blocked by both rules and model, with an explanation
  - rule hit with a model "allow": blocked
  - model block finding with a model "allow": blocked
  - warnings only: allowed
  - model raises: blocked, with `model_error`
  - approve on a blocked job: 409, nothing published
  - approve on a job with no verdict: 409
  - reject from blocked: works
  - blocked phrase in an edited caption at approve: 422, plus a stored pending-edit verdict
  - edit blocked at publish: original kept, job approvable, nothing uploaded
  - safe edit: re-checked and published
  - unchanged text: no re-check
  - graph-mode publish of an unchecked edit on a fixture server: refused
  - Brainstorm job: runs the safeguard
  - policy files parse
- **Prior art:** the existing fixture-pipeline tests (scenario runs with the network faked, approve-mode escalation via the test client, the admin-token guard), and the conftest's temp data dir and fixture-mode isolation.
- **Admin:** type check and lint, plus a manual screenshot check of the Review page for the blocked and allowed scenarios at desktop and phone widths (for the PR description, per the contribution guide).

## Out of Scope

- Any human override of a safeguard block (deliberately excluded; see the decision record).
- Running the safeguard on Trillion Labs' own model. The gate takes the model as an injected callable, so a second backend can come later without changing the contract.
- Making the other checks (facts, links, visual QA, PII, tone) binding. Their planned "override with reason" flow is separate work.
- Checking published posts after the fact, or comments and DMs on the account.
- Automatic rewriting of blocked decks. The operator regenerates.
- Calibration and evaluation of the model's precision and recall on a labelled set (a useful follow-up).

## Further Notes

- A first implementation exists on the `feat/safeguard` branch (one commit, not yet pushed). A two-axis code review of it found these gaps, all covered by the requirements above:
  - Approve is still enabled for a job with no verdict (UI and mock).
  - A fixture-mode server with graph publishing re-checks an edited caption with rules only.
  - A blocked caption edit overwrites the allowed original.
  - The approve-time 422 isn't shown in the panel.
  - The content hash doesn't cover images.
  - The harm category is guessed from the phrase note.
  - The frozen backend contract doc wasn't updated or versioned.
- The sensitive-topics YAML had a quoting error that broke parsing. It is fixed on that branch and needed by the host rules.
- The live safeguard prompt hasn't been run against real decks yet. Tuning the policy wording on real output comes before relying on it for graph-mode posting.
