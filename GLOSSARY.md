# What's On Korea

A card-news publishing agent: an Organization gives a context and a purpose, agents research, verify, write and design English Instagram carousels for foreigners in Korea, and a person approves each one before it posts to the Organization's own Instagram account.

## Language

### Who

**Organization**:
The paying customer: a media outlet, public institution or business that publishes card news under its own name.
_Avoid_: customer, tenant, workspace, account, team

**Member**:
A person's place in one Organization, with a role. One person can be a Member of several Organizations.
_Avoid_: user (when the Organization matters), seat

**Role**:
What a Member may do: Viewer (sees everything), Editor (starts Jobs and Brainstorms, edits Decks), Approver (also approves, schedules and cancels Posts), Admin (also manages Members, Instagram accounts, the Brand kit and billing).
_Avoid_: permission level, manager

**Pilot Organization**:
An Organization whose Instagram accounts we have added as testers on our Meta app, so it can post before Meta approves the app. Other Organizations can do everything except connect an Instagram account.
_Avoid_: beta user, tester (the Meta-side role)

**Read-only Organization**:
An Organization whose trial or subscription has ended: everything stays visible and downloadable, but no Jobs, Brainstorms or Posts run. Deleted 90 days later unless it pays again.
_Avoid_: suspended, cancelled, archived

**Brand kit**:
An Organization's logo, colours and tone, applied to every Deck it makes. One per Organization.
_Avoid_: theme, style, template (templates are slide layouts)

**Organization profile**:
What the agents know about an Organization: what it does, its audience, home region, usual subjects, and things never to say or promote. Read by every Job and Brainstorm.
_Avoid_: settings, persona, about

**Instagram account**:
An Instagram Business or Creator account an Organization has connected. A Post goes to exactly one; a Deck is not tied to any.
_Avoid_: channel, social account, profile, handle

### What gets made

**Prompt**:
What a Member asks for in their own words when starting a Job.
_Avoid_: query, brief (a brief is the researched result)

**Source material**:
Links, files or notes the Organization supplies with a request. The primary source when given: facts taken from it count as verified, and research only fills gaps.
_Avoid_: attachments, references, context

**Brainstorm**:
A conversation with the agent that settles facts, angle, audience and outline before any Deck is made. It can end by starting a Job.
_Avoid_: draft, chat

**Job**:
One request run through the pipeline, from a prompt or a finished Brainstorm to a Deck ready for review. Internal term; customers see the Deck.
_Avoid_: task, run (in customer-facing text)

**Deck**:
The carousel a Job produces: slides, caption and hashtags. What customers create, review and approve.
_Avoid_: draft, card set, carousel (in code), post

**Deck version**:
One saved state of a Deck. Every edit makes a new version; approval pins exactly one.

**Deck kind**:
What a Deck is about: an Event Deck covers one or more Events; a Guide Deck covers practical know-how, rules or an Organization's own announcement.

**Deck quota**:
How many Decks an Organization's plan lets it produce per month. A Deck counts once, when its Job first reaches review; failed Jobs, edits and extra Posts of the same Deck do not count.
_Avoid_: credits, post limit

**Deck in review**:
A Deck that has not been approved yet.
_Avoid_: draft, pending post

**Issue**:
A problem QA or review found in a Deck: blocking or a warning. Issues only advise; a person decides. A Safeguard block is not an Issue.
_Avoid_: error, flag, finding, violation

**Override**:
Approving a Deck despite blocking Issues, with a written reason recorded against the approver. It never approves a Blocked Deck: nobody can override the Safeguard.
_Avoid_: force, skip, ignore

**Safeguard**:
The last check before a Deck can be approved. It decides whether the Deck is safe to post: harmful or controversial content (hate, sexual content, dangerous acts, a Korean tragedy used as a hook or sale, "Sea of Japan") blocks it, and the approver is shown why. Its block is binding: the one check that stops a Deck instead of advising.
_Avoid_: filter, moderation, censor

**Blocked Deck**:
A Deck the Safeguard refused. Nobody can approve it, with or without an Override; it can be regenerated or rejected. If the refused content came from an edit, the Deck stays in review and can be edited again: one bad edit does not kill a good Deck.
_Avoid_: rejected (a person rejects), flagged

**Safeguard finding**:
One reason behind a Safeguard decision: the exact text or image part, the rule it breaks, why it is harmful to foreign readers, and a fix. Findings that block decide; Issues only advise.
_Avoid_: Issue (for these), violation

**Post**:
One publication of an approved Deck version to one Instagram account, now or at a scheduled time. Once published it never changes; a correction needs a new Post.
_Avoid_: publish, upload, media (outside Instagram API code)

**Post on hold**:
A scheduled Post that will not go out until someone acts: its Deck was edited after approval, its time was missed while the server was down, or its Organization became read-only. Approving the Deck again replaces it with a new Post; it can also be cancelled.
_Avoid_: paused, cancelled (a cancelled Post is gone)

**Photo library**:
An Organization's own uploaded photos, each confirmed by the uploader as usable, optionally tied to an Event or place. Used before any other photo source; never shared across Organizations.
_Avoid_: assets, media library, gallery

### What a Deck is about

**Event**:
Something happening at a place and time that a foreigner in Korea could go to: a pop-up, exhibition, festival, performance or experience. The main subject of Decks, but not the only one.
_Avoid_: listing, happening

**Guide**:
Practical, undated information a foreigner in Korea needs (how to use T-money, tax refund rules, an Organization's closure notice). The second subject of Decks after Events.
_Avoid_: topic, article, explainer, notice

**Official source**:
A government or agency site, an operator's own site, or the Organization's Source material. Every fact in a Guide Deck must trace to one; blogs and news can lead to a fact but cannot be its only source.
_Avoid_: trusted source, primary source

**Deck language**:
The language a Deck is written in. English only for now; place names also appear in Hangul.
