# Crossfoot: Product PRD

**Owner:** Ajwad Juman
**Status:** Draft v2 (2026-09-29). Revised after an independent review of v1.
**Companion doc:** `crossfoot-technical-prd.md` (architecture, validators, data model, playground, public demo)
**Supersedes:** `crossfoot-validation-plan.md`

---

## 1. Vision

Crossfoot is an API that turns financial PDFs (bank statements, credit card bills, and later tax invoices) into structured JSON, and checks every result against the document's own numbers.

It answers two questions for every document:
1. **Did we read it correctly?** The parse reconciles with the document's printed totals.
2. **Does the document itself add up?** If we read it correctly and the numbers still don't reconcile, the document is flagged as inconsistent. That is a warning sign lenders care about.

**Name:** Crossfoot (the accounting check that row totals equal column totals). `crossfoot.dev` and the PyPI name `crossfoot` were available as of 2026-09-24.
**Tagline:** Parsing with proof.
**Hero subline (working):** Parse bank statements, credit card bills, and invoices into JSON via API. Every response is checked against the document's own totals, and documents that don't add up are flagged.

### 1.1 Goals

1. Find out, for no more than the cost of a domain, whether small fintech and accounting teams will **pay** for verified document parsing.
2. If they will, reach $1k MRR.
3. Keep ongoing effort low once built. Some maintenance is unavoidable (§11).

### 1.2 How the work gets done

- Code is written mostly with Claude Code. This PRD does not estimate coding hours.
- The real bottlenecks are **debugging parsers against real documents** and **talking to buyers**. Neither can be delegated to a coding tool, so the calendar is set around them.
- Test documents: 3 own bank accounts, 2-3 more from family and friends with consent, 7 own credit cards, and UAE samples from acquaintances.

---

## 2. Market decision

**Decided: India first, UAE second, country-neutral brand.**

- **India at launch:** Indian bank statements and card bills; GST invoices later.
- **UAE next:** sample-driven. Collect UAE statements from acquaintances during validation; build UAE bank layouts after the Indian ones pass, or earlier if a paying buyer asks.
- **Any other country:** only when a paying customer asks and samples exist.
- **Brand:** the name, domain, API schema, and docs contain no country. Every document carries a `currency` and `locale`. Country-specific rules (GSTIN for India, VAT TRN for the UAE) live in per-country validator plugins.
- **Currencies:** INR, AED, and USD.

**Why this works technically:** the bank statement check (previous balance ± amount = new balance) is the same in any currency. Adding a country means new bank layouts and number/date formats, not a new product. Invoices are the exception: each country's tax rules need their own validator.

---

## 3. Buyers

### 3.1 Primary (statements and cards)

| Segment | Their pain | Who decides |
|---|---|---|
| Small lenders and lending-tech (MSME lenders, small NBFCs, loan marketplaces) | Customers upload statements for income and cash-flow checks. Ops staff read them by hand, or a script covers a few banks. They also need to spot statements that don't add up. | Eng lead or CTO evaluates; founder or ops head approves |
| Accounting and bookkeeping SaaS, CA-tech | Statement import and reconciliation are core features; every new bank format becomes a support ticket. | Founder or CTO |

**Open question for interviews:** lenders may want analysis (income, obligations, risk flags) rather than raw JSON. Ask directly. If the answer is "analysis", that's a Pivot signal (§6.8).

### 3.2 Secondary (invoices, build phase 3)

GST, AP, and expense tools that ingest purchase invoices and reconcile input tax credit.

### 3.3 UAE

Buyers not yet identified. Use UAE acquaintances both for samples and for introductions to anyone in UAE lending or accounting.

### 3.4 Not targeted

Banks and large lenders (slow procurement, compliance demands, Account Aggregator in India). Individuals and CAs converting their own statements (they want a UI, and they are the free-demo audience, not buyers). Small NBFCs are still targeted, but expect vendor due-diligence questionnaires.

---

## 4. Competitive position

| Player | What they do | Relevance |
|---|---|---|
| FinBox BankConnect | India-native bank statement analysis for lenders, including fraud and income analytics | **Closest competitor for the lender segment.** Sells analysis, not just JSON. No public pricing. |
| Perfios and similar | Enterprise statement analysis in India, quote-only | Aimed at larger lenders (assumption; confirm in interviews) |
| Nanonets | General document AI; statements are one of many types | Broad, not verification-focused |
| DocuClipper | Statements, invoices, receipts | Its pricing page showed about $0.17-0.33/page when the reviewer checked (2026-09-29); an earlier comparison page said $0.19-0.05. Re-verify before quoting. |
| BankConv | Consumer converter, $2.99/document or $29-79/month | 21 active subscriptions on TrustMRR: the converter market can be small |
| Apify bank statement actor | US/EU statements with a paid balance-reconciliation check | Shows the reconciliation idea itself isn't unique |
| AWS Textract, Google Document AI | Generic extraction | Buyers' do-it-yourself option |
| Account Aggregator (India) | Consent-based bank data sharing with no PDFs | Reduces PDF demand among regulated lenders |

**Honest differentiator:** reconciliation alone is copyable. Crossfoot's case is the combination of (a) a verification result in every response, (b) a document-inconsistency flag lenders can act on, (c) bank layouts for India and the UAE, including password-protected PDFs, (d) country tax validators, and (e) simple self-serve pricing where competitors are quote-only. Whether buyers value this combination is what validation tests.

**Decided: no competitor teardown during validation.** Don't claim "we pass where they fail" without evidence.

---

## 5. Core feature: the document-inconsistency flag

**Decided: in from the start, and billed.**

Every document gets one of these outcomes:

| Outcome | Meaning | Billed? |
|---|---|---|
| `verified` | Parse is structurally clean and every check passes | Yes |
| `document_inconsistent` | Parse is structurally clean on a trusted layout, but the document's own numbers don't reconcile | Yes |
| `parse_failed` | We couldn't read it reliably | No |
| `unsupported` / `scanned` / `bad_password` | Layout not supported, image-only PDF, or wrong password | No |

**Guardrail:** `document_inconsistent` is only returned when we are confident the parse is right:
- the layout is **trusted** (it has passed the held-out test in §6.8), and
- every row is structurally clean (valid dates within the statement period, exactly one of debit/credit, amounts parsed in the expected format), and
- the breaks are localized to specific rows, with the expected and printed values shown.

Anything short of that is `parse_failed`. Without this guardrail, a parser bug would tell a lender that their borrower's statement looks suspicious.

**Wording discipline:** always "doesn't reconcile" or "inconsistent". Never "tampered", "fraud", or "forged". Crossfoot reports arithmetic, not intent. A careful forger can edit balances consistently, and this check won't catch that.

**Known limitation:** for credit card bills the inconsistency flag is weaker: the check can say the bill doesn't add up, not which row is wrong.

---

## 6. Phase 0: Validation

### 6.1 Goal

Decide Go, Narrow, Pivot, or Kill using real documents, real conversations, and **real payments**, spending nothing except the domain.

### 6.2 Timeline (about 7 weeks)

| Weeks | Stage | Focus |
|---|---|---|
| 1-3 | A: local playground | Build parsers, redact training statements, outreach |
| 4 | B: build demo | Public demo, waitlist, manual key issuance |
| 5-7 | B: signal window | Demo live, waitlist conversations, paid packs, outreach continues |
| End of 7 | Decision | Fill in the scorecard (§6.8) |

The public demo goes live only after the technical gate passes. If it doesn't pass by the end of week 3, see the technical gate rules in §6.8.

### 6.3 What gets built

**Stage A: local playground.** Upload, parse, see the result, correct mistakes, keep every input, output, and correction. Runs only on your laptop. Details in the technical PRD.

**Stage B: public demo on `crossfoot.dev`.**
1. **Public page:** supported banks and issuers, sample JSON from synthetic statements, and a live upload that is ephemeral, rate-limited, and returns JSON, CSV, or Excel.
2. **Waitlist → paid access:** a form for email, company, use case, and monthly volume. You talk to each serious requester. If they're a potential buyer, you offer paid API access and docs through a founding prepaid pack (§8), paid manually. The waitlist shows demand; the payment shows willingness to pay.
3. **Unsupported bank capture:** "Want us to add your bank? Leave your email."

Public uploads are ephemeral (metadata only). The playground's store-everything rule never applies to the public demo.

### 6.4 Rules

1. No spending until a trigger in §6.10 is met. The one planned spend is the domain at the start of Stage B.
2. Outreach starts in week 1.
3. **Real documents:**
   - Stay on your laptop by default, with full-disk encryption.
   - **Training statements** may be shared with Claude or Claude Code **only after proper redaction**: the text is actually deleted from the PDF (not covered with black boxes), including names, addresses, account and card numbers, UPI IDs, phone numbers, emails, and counterparty names in narrations. Verify by re-extracting the text. Amounts and dates stay.
   - **Held-out statements** (§6.8) are never shared with any tool, redacted or not.
   - Third-party documents follow the same rules, and only if the owner's consent covers it (see the consent message below).
4. Written consent for every document that isn't yours.
5. Kill criteria are written now (§6.8) and not changed later.

### 6.5 Document collection

Consent message (family, friends, UAE acquaintances):

> I'm testing a tool that reads bank statement PDFs. Could I use 3 months of your statement as a test? It stays on my laptop and is used only for testing. I may share a redacted copy, with your name, account number, and other personal details removed, with an AI coding tool to help build the parser. I'll delete everything when I'm done or whenever you ask. If it's password-protected, send the password separately.

For concierge samples from companies: they contain the company's customers' data, so a chat message isn't enough. Use a short written data-processing note (what you'll do, how long you'll keep it, that you'll delete it on request) and never share their samples with any AI tool unless they explicitly agree.

**Targets:** 3 months per account and per card. Record which month is held out (§6.8) the moment each document arrives.

### 6.6 Week-by-week

**Weeks 1-3 (Stage A)**
- Claim free names (PyPI stub, npm, GitHub). Note that PyPI can reclaim unused placeholder names; don't rely on it.
- Collect documents; assign held-out months immediately; redact training statements.
- Build the playground: bank statements first (3+ layout families), then credit cards (2-3 issuers).
- Build a list of 50 target companies; start outreach (§6.7); send warm-intro requests; one community post.
- Ask UAE acquaintances for samples and for any introductions.
- Concierge every sample you receive: parse, correct, return the JSON within 24 hours, log how much fixing it needed.
- End of week 3: score the technical gate on held-out documents.

**Week 4 (Stage B build):** buy `crossfoot.dev`, build and deploy the public demo, prepare the founding pack offer.

**Weeks 5-7 (signal window)**
- Announce in dev and fintech groups, r/developersIndia, Byte After Eight.
- Reply personally to every waitlist request within a day. Offer the founding pack to buyers. Issue keys by hand after payment.
- Keep outreach running.
- End of week 7: fill in the scorecard and write down the decision and why.

### 6.7 Outreach

**Channels, in order of expected yield:** warm intros (alumni, work network, LinkedIn), the public demo (from week 5), Byte After Eight, fintech and CA-tech WhatsApp and Slack groups, r/developersIndia, cold DMs.

**Volume:** about 15-20 messages a week for 7 weeks, 100+ touches in total. At a typical 10% reply rate, that gives roughly 10 replies and 5-8 calls. The gate below is set to that funnel.

**Cold DM:**
> Hi {name}, I'm building an API that turns bank statement and credit card PDFs into JSON and checks every parse against the document's own balances, so bad parses and statements that don't add up both get flagged. Before building the full product I'm running it by hand for a few teams: send me 2-3 sample statements (scrubbed is fine) and I'll return parsed JSON plus the check result within 24 hours, free. Worth 15 minutes to see whether this fits how you handle statements today?

**Discovery questions (past behavior, not hypotheticals):**
1. Walk me through the last time a customer gave you a bank statement or invoice. What happened next?
2. Who handles it, how long per document, how many per week?
3. What do you use today, and what does it cost you in money or staff hours?
4. Tell me about the last time a parse was wrong. What was the impact?
5. Have you had statements that turned out to be edited or wrong? How did you find out?
6. Which banks cause the most trouble? Mostly digital PDFs or scans?
7. Do you need the raw transactions, or analysis on top (income, obligations, flags)?
8. What would have to be true for you to switch? Who signs off? Any data residency or compliance requirements?

### 6.8 Gate (pre-committed)

**Technical gate: scored only on held-out documents.**

*Held-out set:* before writing any parser, set aside one month per bank account and card. Every sample from anyone else is also held out by default. You never look at held-out documents while building, and they are never shared with any tool. Parsers are built on training documents only; the gate is scored only on held-out ones. This is the same idea as a train/test split: a parser tuned to the documents it's tested on will always look good.

| Metric (held-out only) | Target |
|---|---|
| Bank statements `verified` with no manual fix | 90%+ across 3+ layout families |
| Card bills `verified` | at least 1 held-out bill verified for each of at least 2 issuers |
| Amount or balance errors (compared to your corrected version) that the check flagged instead of returning as `verified` | all of them, on the held-out set |

*If bank statements come in at:*
- **90% or more:** pass.
- **80-89%:** one extra week of parser work, then rescore once. The demo slips a week.
- **Under 80%:** stop and reconsider the approach before spending anything.

**Market gate**

| Track | Metrics |
|---|---|
| **Payment (required)** | At least 1 company pays for a founding prepaid pack |
| Outreach track | 5+ conversations; 3+ describe a workaround that costs them money or staff time today; 2+ share real samples |
| Demo track | 10+ waitlist requests from companies (not personal emails); 3+ paid or approved keys used on 2+ different days; 5+ conversations started from the waitlist |

**What does not count:** page views, one-off demo uploads, CSV or Excel downloads, bank requests from individuals, upvotes, likes.

**Decision rules:**

| Outcome | Condition | Action |
|---|---|---|
| **Go** | Technical gate + payment + at least one track | Release spend (§6.10) and start the build phases (§7) |
| **Strong Go** | Technical gate + payment + both tracks | Same, with more confidence |
| **Wait** | Market signal is there (payment or a track met) but the technical gate failed | Keep the waitlist open and fix parsers for up to 2 weeks, then rescore. Don't sell beyond what works. |
| **Narrow** | Payment or strong interest only in one segment, document type, or country | Focus on it; cut the rest |
| **Pivot** | Buyers consistently want analysis rather than JSON, or only the inconsistency flag | Rework the demo for that; one more validation cycle |
| **Kill** | No payment and neither track met by end of week 7 | Stop; move the parsers into PocketLog |

### 6.9 Not done in this phase

No payment gateway (manual payments only); no self-serve keys; no LLM fallback; no OCR for scans (count them); no invoice parser (validators only); no per-bank SEO pages; no open-source client; no competitor teardown.

### 6.10 Spend ladder

| Trigger | Allowed spend |
|---|---|
| Stage A | Nothing |
| Technical gate passed (Stage B) | Domain `crossfoot.dev`. Hosting and database on free tiers only. Notifications through your own email account or a CLI check, no paid email service. |
| Go | Paid hosting if free tiers limit you, a payment gateway, transactional email, error monitoring |
| Repeatable revenue | LLM fallback budget, OCR compute, paid tools |

### 6.11 Payments during validation

Manual: UPI or bank transfer, invoice issued by hand under your chosen legal setup. Record every payment and the pages credited (the technical PRD tracks prepaid page balances). Keys are issued by hand after payment.

---

## 7. Build phases after Go

### 7.1 Phase 1: Bank statements, production-ready (build month 1)
- 8-10 Indian banks; UAE banks if samples and a buyer exist.
- LLM fallback and template learning loop.
- **Success:** 95%+ of held-out statements `verified` without the LLM; founding customers renew or top up their packs.

### 7.2 Phase 2: Credit cards + open API (build month 2)
- 5-8 card issuers.
- Payment gateway, self-serve prepaid packs, docs, pricing page. Whether key approval stays manual is decided here, based on how valuable the approval conversations turned out to be.
- **Success:** 5+ paying customers.

### 7.3 Phase 3: Invoices + launch (build month 3)
- GST invoices, starting with the most common layouts seen in real samples.
- Public launch: Show HN, a Byte After Eight reel, per-bank SEO pages.
- **Success:** first paying invoice customer, or clear evidence that invoices should wait.

---

## 8. Pricing

**Decided: ₹10 per page, sold as prepaid packs.**

| Pack | Price | Pages | Per page |
|---|---|---|---|
| Founding pack | ₹999 | 100 | about ₹10 |

- Only `verified` and `document_inconsistent` pages consume credits (§5).
- The free public demo is capped by rate limits, not credits.
- **Defaults to confirm (not yet decided by you):**
  - Larger packs, created when a customer asks for more volume (for example ₹4,999 for 600 pages), so heavy users get a better rate.
  - USD pack: $12 per 100 pages. AED pack: AED 45 per 100 pages. Both are close to ₹10/page at current rates; adjust once you see who pays.
  - Taxes on top, per your legal setup.

**MRR math:** at roughly ₹96 per dollar (the reviewer's 2026-09-28 figure; re-check), $1k/month is about ₹96k, which is about **9,600 billed pages a month**. For example, 10 customers each using about 1,000 pages a month. That's far more achievable than at ₹1-3 per page.

**Positioning of the price:** ₹10 (about $0.10) is at the upper end of what global converters charge per page. Justify it with verification and the inconsistency flag, not with extraction alone. If buyers push back hard in interviews, that's pricing data. Record it.

---

## 9. GTM

### 9.1 Positioning

"Parsing with proof." Lead with trust: every response says whether the parse reconciles, and flags documents that don't add up.

**Claim discipline:**
- Bank statements: "amounts and balances verified against the running balance on every row."
- Credit card bills: "reconciled against the printed summary."
- Invoices: "tax IDs, tax totals, and HSN codes checked."
- Inconsistency flag: "doesn't reconcile", never "tampered" or "fraud".

### 9.2 Channels, in order of expected payoff

1. **Direct outreach**, 15-20 messages a week, warm intros first.
2. **Public demo + waitlist**, the landing page for every other channel; every serious waitlist request gets a conversation and a founding-pack offer.
3. **Communities:** alumni, fintech and CA-tech groups, r/developersIndia, UAE contacts.
4. **Byte After Eight:** a reel showing a statement that doesn't add up getting flagged.
5. **Show HN / dev.to:** the technical story.
6. **SEO** (build phase 3 onward): per-bank pages, docs on your own domain.
7. **Open-source Python client:** developer trust.

### 9.3 Realistic timeline

- First payment: during the validation window (it's a gate requirement).
- $1k MRR: about 10 customers at ~1,000 pages/month each. Plausible within 6-9 months of Go if the gate's payment signal is real.

---

## 10. Relationship to PocketLog

- **Separate products.** Different buyers and different trust promises.
- PocketLog can become an internal user of the API later.
- PocketLog's needs don't count toward the gate.
- If Crossfoot is killed, the parsers become a PocketLog feature.

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| The technical gate looks good only because parsers were tuned to the test documents | Held-out set, never viewed while building, never shared with tools |
| `document_inconsistent` fires because of our own bug, and a lender acts on it | Only on trusted layouts with structurally clean parses (§5); wording never alleges fraud |
| Buyers want analysis, not JSON (FinBox territory) | Asked in every interview; Pivot row in the gate |
| Small test set doesn't generalize | Held-out and third-party samples score the gate |
| Real documents leak through AI tools | Redaction must delete text; held-out and third-party documents never shared |
| Public demo receives strangers' financial documents | Ephemeral processing, accurate privacy notice, rate limits, sandboxed parsing (technical PRD) |
| Demo usage mistaken for demand | Only company waitlist requests, repeat key usage, conversations, and payments count |
| UAE adds scope without buyers | Samples collected now; layouts built only after India passes or a buyer pays |
| ₹10/page is too high for Indian buyers | Founding packs test it cheaply; record every pushback |
| Banks change PDF layouts without notice | Per-layout pass-rate alerts |
| Account Aggregator absorbs regulated-lender demand | Target small lenders, accounting tools, non-AA flows, and the UAE |
| Not autopilot | Expect a few hours a week for layout fixes and support after launch |

---

## 12. Out of scope

- Countries beyond India and the UAE until a paying customer asks.
- Scanned-document OCR during validation.
- Competitor teardown.
- Analysis products (income estimation, risk scoring) unless interviews trigger a Pivot.
- Claims of tamper or fraud detection.
- A consumer app.
- Matching transactions across documents.
