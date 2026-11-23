# Hiring and Leader Reviews — the rules behind the People section

**Chick-fil-A Wharncliffe & Wonderland — London, Ontario**

**This is legal information and HR advisory support, not legal advice.**
It is not privileged. It explains the Ontario rules that shaped the People
section of Team Hub (see [../people.md](../people.md)), gives the wording
the tool uses, and says where a licensed lawyer has to look before anyone
acts. It follows this framework's usual shape: issue framing, risk,
options, recommended path, documentation, escalation flag.

**How far to trust it.** It was prepared on 20 November 2026 from two
research briefs that were each independently reviewed the same day.
**No primary source could be opened** from the build environment
(ontario.ca, e-Laws, ohrc.on.ca and canlii.org were not reachable), so
every rule below rests on search extracts of the official pages plus
law-firm bulletins. Each rule is tagged:

- **[Checked]** a supporting extract of the official page, or a reputable
  firm bulletin, was seen on review. It does not mean the provision was
  read on e-Laws.
- **[Partly checked]** the rule was confirmed but a detail was not (which
  detail is stated).
- **[Not checked]** it rests on the authors' knowledge only.

Part E lists every open point. **Verify a section number or a figure on
e-Laws before relying on it for a decision** (standing rule 2 of the
[escalation matrix](./escalation-matrix.md)).

---

## Issue framing

**Hiring.** Since 1 January 2026, ESA Part III.1 and O. Reg. 476/24 make a
public job posting a regulated document for an employer with 25 or more
employees: it must state pay, say whether the vacancy exists, disclose AI
use, and may not ask for Canadian experience; every interviewed applicant
must be told where things stand within 45 days; postings, forms and
notices must be kept three years. The Human Rights Code governs what a
posting, a form and an interviewer may ask. The AODA employment standard
requires accommodation notices at three points.

**Leader reviews.** No Ontario statute was found that forbids an
AI-drafted, human-decided review of an existing employee, and none was
found that requires telling employees that AI is used. Both are negative
findings: a search can fail to find a rule, it cannot prove there is none
(row B9). The legal weight sits elsewhere: the Human
Rights Code (what may be assessed, and the duty to inquire before treating
a possible disability as a performance problem), ESA and OHSA leave and
reprisal protection, the ESA electronic monitoring policy (if app activity
is ever used), common-law privacy, and the later use of a review as
evidence for or against the store.

## Risk assessment

| Area | As built | What would raise it |
|---|---|---|
| Hiring: postings, form, notices, records | **Low.** The tool refuses to publish a posting that lacks a required statement. The fixed first step of the form asks nothing on a prohibited ground, and a posting cannot be published while one of its own questions uses a listed word about one, unless the Operator has confirmed that exact question as being about a guest or the work (listed words only, so the Operator still reads every question). It tracks every 45-day deadline and keeps the three-year records. | Advertising the job somewhere else without the same statements; a hand-written question that asks about a ground in words on no list; an Operator confirmation given without reading the question; a live posting left failing after a wage or contact change in Settings; interviews held but never logged; a leader's note about a protected ground; letting the Retention list sit. |
| Hiring: AI-assisted scoring | **Medium, untested.** No HRTO decision on AI scoring was found. Disclosure is built in; the Code exposure is systemic. | Switching it on before a lawyer has looked; leaders deferring to the suggestion. |
| Leader reviews: human-written | **Low to medium.** Protected-context questions stop a review before it is final; nothing happens automatically. | A review used toward discipline or dismissal without the escalation matrix; reviewer notes about health, leave or complaints. |
| Leader reviews: AI drafting | **Medium.** Names are removed, notes in which a word list finds health, family, leave or complaint wording are held back (listed words only: a note that says it another way is sent), and the draft is checked by code and shown only after the reviewer's own rating, but leaders' work notes go to an outside provider. | Switching it on before leaders are told in writing and the provider's terms are read. |

## Options

1. **Run both features without AI.** Everything works; the legal exposure
   is the ordinary exposure of structured hiring and written reviews.
2. **Switch AI on for leader reviews only**, after a first cycle without
   it, written notice to leaders and a read of the provider's terms.
3. **Switch AI on for hiring as well**, after a lawyer has looked at it.

## Recommended path

Start with option 1 for the first review cycle and the first hiring round.
Move to option 2 when the Part C notice has gone to leaders. Treat option 3
as a **Tier 2** item: have counsel or e2r look at AI-assisted applicant
scoring, the provider's data terms and the applicant privacy notice before
the switch goes on. Do not use Team Hub activity in reviews until the
electronic monitoring policy has been changed and given to every employee
(Part B, rule B1).

## Documentation

Part C holds the wording, exactly as the tool prints it or, where it is
for the Operator to send, adapted so that every sentence is true of the
tool as built.

## Escalation flag

Building and running the tool is **Tier 1**. Switching on AI for hiring,
any pay link to review results, and the retention period are **Tier 2**.
Using a review toward ending anyone's job, and any refusal to hire that
touches a protected ground, follow Part D.

---

## Part A — Hiring: the rules that shaped the tool

Source: the reviewed hiring brief of 20 November 2026. Statute and
regulation references are to the *Employment Standards Act, 2000* (ESA),
O. Reg. 476/24, the *Human Rights Code* (Code) and O. Reg. 191/11 (the
AODA Integrated Accessibility Standards, IASR).

| # | Rule | Source | Status | What the tool does |
|---|---|---|---|---|
| A1 | **The 25-employee test.** The posting rules do not apply to an employer with fewer than 25 employees on the day the posting goes up. The count is of people, not full-time equivalents; part-time and casual each count as one; all Ontario locations and related employers count together. | O. Reg. 476/24 s. 1; Ministry guide | [Checked] | Treats every posting as covered. It does not record the headcount: note it yourself on each posting date. |
| A2 | **What counts as a posting.** An external posting for a specific position advertised to the public in any way. Not a general "we're hiring" sign or campaign, and not a posting restricted to existing employees. | ESA s. 8.1; O. Reg. 476/24 s. 2 | [Checked] | Every posting on `/careers` is treated as one. |
| A3 | **Pay.** The posting must state the expected compensation or a range no wider than $50,000 a year. "Compensation" means wages: tips and discretionary bonuses cannot be used to state it. | ESA s. 8.2; O. Reg. 476/24 ss. 3, 4 | [Checked] on the numbers; [Partly checked]: the tips exclusion rests on one firm bulletin, and no official conversion of the cap to an hourly range was found | Refuses to publish without both figures; refuses a spread above $50,000 at 2,080 hours a year (about $24.03 an hour); flags "tips" and "bonus" for a person to read. |
| A4 | **Minimum wage.** From 1 October 2026: general $17.95, student $16.90. The student rate applies only to students under 18 who work 28 hours a week or less in the school year or work during a school break. **Verify current rate**: it changes every 1 October. | ESA s. 23.1 | [Checked] by several firm and institutional sources; the official table was not opened | Both figures are settings the Operator updates, each shown with the day it was last confirmed; after every 1 October the tool asks for them to be checked again, and lowering one needs a tick. A posting cannot start below the general rate unless the student box is ticked, and then it prints the condition (in full on the posting, as "(student rate)" beside the range on the list and the form) and never says "students only". A live posting that a new figure leaves below the minimum is named to the Operator at once and stays public until it is fixed or closed. |
| A5 | **Existing vacancy.** The posting must say whether it is for an existing vacancy. | ESA s. 8.5 | [Checked] | Refuses to publish until one of the two statements is chosen. |
| A6 | **Canadian experience.** A posting and its application form may not include any requirement related to Canadian experience. | ESA s. 8.3 | [Checked] | The form has no such question; the wording check flags the phrase in a posting. |
| A7 | **The AI statement.** If AI is used to screen, assess or select applicants, the posting itself must say so. A plain statement is enough. It applies equally when a third party does the screening. | ESA s. 8.4; O. Reg. 476/24 s. 2 (definition) | [Checked]; not settled: AI built into a job board the store merely posts on | AI assist can be switched on for a posting only while it is a draft, and a posting with it on cannot be published unless AI can really run (the Settings switch and a server key), so the statement is on the posting from the day it goes live. A posting that has been public without the statement (it never had AI, or AI was switched off and the posting was opened meanwhile) cannot use AI again: the job needs a new posting. AI runs only on applications made while the statement was showing. The tool never prints "no AI is used". Copies of the posting placed elsewhere have to carry the statement too (C4). |
| A8 | **The 45-day duty.** Anyone interviewed for a posting must be told whether a hiring decision has been made within 45 days after their interview, or their last one. Interviews more than 45 days apart each need a notice. "No decision yet" is a valid answer. Any method is allowed, including a status page the applicant has a link to. | ESA s. 8.6; O. Reg. 476/24 s. 5; Ministry guide | [Checked] on the 45 days; [Partly checked] on the guide points; not settled: whether a second notice is owed once a decision is later made | Every attended interview starts a clock; the Notices page and the menu count show what is owed and overdue (and who is past the store's own 30-day aim); the leader logs who was told, how, when and in what words. An interview held before a decision was recorded can still be logged afterwards. An attended interview whose notice is still owed cannot be changed to "not held" without a tick, and once the notice is overdue only the Operator can. Someone told "no decision yet" stays listed until the decision is passed on: the second notice is sent because whether it is owed is not settled. |
| A9 | **What counts as an interview.** A meeting in person or by technology where questions are asked and answered to assess suitability. Preliminary screening before applicants are selected for such a meeting is excluded. | O. Reg. 476/24 s. 2 | [Checked] on the text; unsettled: a short phone screen, a one-way video | Treats any interview a leader marks as attended as an interview, phone and video included. The quick written questions are screening, not an interview. |
| A10 | **Three-year records.** Keep a copy of every posting and its application form for three years after it comes down, and a copy of what each interviewed applicant was told for three years after the day it was given. They must be available for inspection. | ESA ss. 15(7.1), 15(7.1.1), 16 | [Checked]; not settled: whether "application form" means the blank form or each completed one | Keeps an exact copy of the posting with the blank form at every publish, change and close, never deleted; keeps each notice given to an interviewed applicant, with the applicant's name, for three full years after the day it was given (through the third anniversary; longer while a hold is on); one download gives all of it. A notice logged for an applicant who was never interviewed is outside s. 15(7.1.1): its wording and the name are removed with the rest of that applicant's details, as the privacy notice says (C5). The tool reads "interviewed" from the interviews logged for the applicant, and reads it widely so the record is not lost by a missed tap: an interview marked Attended, or one logged and never marked, keeps the notice; only an applicant with no interview logged, or with every one marked did not show, cancelled or rescheduled, is treated as never interviewed. Completed applications follow the Retention setting instead (see C5). |
| A11 | **What a form may ask.** A form or interview question may not classify by a prohibited ground: race, ancestry, place of origin, colour, ethnic origin, citizenship, creed, sex (including pregnancy), sexual orientation, gender identity or expression, age, record of offences, marital status, family status, disability. Asking can itself be the breach. The only age question is "18 or over?"; the only citizenship question is "legally entitled to work in Canada?". | Code ss. 5(1), 23(1)–(3); OHRC guidance | [Checked] on the grounds and the two permitted questions; [Partly checked]: that asking is itself the breach (the hiring research rates that point medium confidence), SIN timing, availability wording | The first step of the form asks for name, contact, the two permitted questions, availability by day and time (never why), how they heard of us and optional experience. The two answers are never on a scoring screen. The second step asks the posting's own questions, which a leader writes. The publish check holds back a question for applicants or for the interview that uses a listed phrase about age, pregnancy or family, religion, citizenship or where someone is from, health or disability, a record of offences, gender or orientation, a SIN, a photo or a licence, its plural, or any word on the lists used for review notes (health, family, religion, age, origin, gender, leave). A held question is reworded, or the Operator alone confirms, per question and on the record, that it is about a guest or the work and not the applicant; the confirmation ends if the question is changed. **The check catches listed words only and cannot read a question for meaning: the Operator reads every question before publishing.** |
| A12 | **Neutral rules that screen a group out.** A requirement that disadvantages a protected group infringes the Code unless it is reasonable and bona fide and cannot be accommodated. This reaches scoring rules and AI output. | Code ss. 9, 11 | [Checked] on s. 11; its application to AI is untested | No knock-out rules: an answer only ever raises a flag for a person. No total, no average, no ranking. The AI sees answers only. |
| A13 | **Accommodation notices.** Tell the public that accommodation is available in recruitment; tell an applicant selected for an interview that it is available for the interview; tell the person you make an offer to about the accommodation policies. Applies at any size. | IASR ss. 22, 23, 24 | [Checked] | Printed on every posting and the form (with a named contact, required before publishing and not removable while a posting is live), in the interview invitation and in the offer message. For the written quick questions a leader can give more time on the applicant's link or enter answers the applicant gave another way; no reason is asked for or kept. |
| A14 | **Minimum ages.** 14 for a restaurant's serving areas, 15 for its kitchen. Proof of age only after a conditional offer. | Reg. 851 s. 4; Ontario "Minimum age for work" page; Code s. 10(1) | [Checked] | Printed on every posting and in the offer checks. The tool does not verify age: a person does, after the offer. |
| A15 | **At the offer.** A trial shift is paid work. An employer with 25 or more employees must give a new hire written job information before their first day (legal and operating name, contact details and a contact name, first work location, starting wage, pay period and pay day, expected hours). In force **1 July 2025**. | ESA s. 1(1); O. Reg. 285/01 s. 1.2 | [Checked] on the duty and date; [Partly checked]: the list of items was not seen in an extract | Both are reminders in the offer checks. The tool does not produce the written information. |
| A16 | **Organizational liability.** What a leader, or a vendor acting for the store, does in hiring is treated as the store's act. | Code s. 46.3 | [Not checked] (firm summary only) | Shown as the reason a person, not the tool, decides. |
| A17 | **Time limit for a Code application.** Generally one year from the incident; late filing is possible for good-faith delay. | Code s. 34(1), (2) | [Checked] | Why the Retention setting cannot be set below 12 months. |
| A18 | **Applicant privacy.** No general privacy statute governs this employer's applicant records (PIPEDA covers applicant data only in federal works; Ontario has no private-sector Act). Common-law privacy torts, contract and the Code still apply. No rule was found that specifically governs sending applicant answers to an outside AI provider. | OPC guidance; a negative finding | [Checked] on PIPEDA; the AI-provider point is a negative and was [Not checked] beyond "nothing contrary found" | A privacy notice above the consent tick (C5); a separate store; names and contact details are never sent to the AI; removal after the stated months is a button the Operator presses. The form answers the same way whether or not an email or phone number has applied before, so it cannot be used to find out who has applied; a repeat is flagged for a leader instead. |
| A19 | **Messages to applicants (CASL).** Most recruitment messages are not "commercial electronic messages", because they promote nothing. One that carried a promotion would be, and would need consent, sender identification and a way to unsubscribe. | CASL s. 6; CRTC FAQ | [Partly checked]: one search extract of the FAQ; nothing found on interview texts specifically | The four ready messages are about the application only. The tool sends nothing: a leader sends each from their own app. Never add an offer, a coupon or marketing to one. |

Also relevant and **not built**: police and consumer-report checks (if
ever wanted, after a conditional offer, with written consent first, and a
call to counsel: Tier 2); the job-posting-platform fraud rules, which
would apply only if the careers pages ever listed another employer's jobs.

## Part B — Leader reviews: the rules that shaped the tool

Source: the reviewed leader-review brief of 20 November 2026.

| # | Rule | Source | Status | What the tool does |
|---|---|---|---|---|
| B1 | **Electronic monitoring policy.** An employer with 25 or more employees on 1 January must have a written policy saying whether and how it monitors employees electronically and what the information may be used for, give a changed policy to every employee within 30 days of the change, and keep each version three years. It is a transparency rule only: it creates no right not to be monitored. | ESA Part XI.1, s. 41.1.1; s. 15(8.2) | [Checked]; that in-app activity logs are "electronic monitoring" is an inference (the cautious one) | **Using Team Hub activity in reviews is not built.** Reviews rest on what a reviewer saw and wrote. If activity data is ever wanted, the policy has to be changed and delivered first; a draft paragraph is in Part C. |
| B2 | **Human Rights Code.** Reviews, pay, promotion and discipline are all covered. Intent is not needed. A neutral measure that disadvantages a protected group is discrimination unless it is bona fide and cannot be accommodated. | Code ss. 5(1), 9, 11 | [Not checked] on review (search limit); long-settled law | Standards are the store's own, written as behaviour at work. A word-list check flags protected-ground wording in notes, comments and AI output. Reviewer guidance sits beside every text box. |
| B3 | **Duty to inquire.** Where the store knows or suspects a shortfall may be linked to a disability, it must ask and offer accommodation before treating it as a performance problem. The same care is prudent for pregnancy, family status and creed. | Code s. 17; a June 2026 HRTO decision reported by Fasken | [Partly checked]: the duty was confirmed; s. 17 and the case name were not | Before a review can be finalised the reviewer answers six questions. Any "Yes or not sure" puts it on hold for the Operator, outside the tool. The leader's page invites them to ask for support or an accommodation at any time. |
| B4 | **Values must be behavioural.** A leader may not be scored on belief, religious practice or participation, or "fit" that stands in for a protected ground. | Code s. 5; OHRC creed policy | [Partly checked] | The model is loaded by the Operator. The go-live checklist asks that every standard be about behaviour at work. **The store's standards need this read once before the first cycle.** |
| B5 | **Leave is neutral.** No penalty because someone took or is eligible for a protected leave. Service keeps accruing. | ESA Part XIV, ss. 52, 53, 74 | [Partly checked] | Time-away dates (dates only, never the reason) take the notes inside them out of the evidence, and an AI draft made before the dates were marked is no longer shown. A note in which the word list finds health, family, time-away or complaint wording (everyday words included: "appointment", "was away", "missed two shifts") is not sent to the AI, and a "To work on" note of that kind prompts the reviewer to mark time-away dates instead. The list catches listed words only, so the page tells the reviewer to leave such notes out themselves. Leave is the first "before you finish" question. |
| B6 | **No reprisal.** No penalty because someone asked about their rights, raised a safety concern, refused unsafe work, made a complaint or filed a WSIB claim. The employer bears the burden. | ESA s. 74; OHSA s. 50; Code s. 8 | [Checked] as to OHSA s. 50; otherwise [Not checked] | Complaints are never to be written in a note (guidance, word-list check) and are a "before you finish" question. |
| B7 | **Paid time.** Time an hourly leader is required or allowed to spend on their reflection, reading their review or in the review meeting is working time. | O. Reg. 285/01 s. 1.1 | [Checked] | The leader's pages say so. The tool does not record the time: payroll has to. |
| B8 | **Accessibility in performance management.** Take a leader's accessibility needs and any accommodation plan into account, and provide the review in an accessible format on request. | O. Reg. 191/11 s. 30 (and s. 26) | [Checked] on s. 30 | The leader's page says to ask the Operator for another format; there is a print view. |
| B9 | **No AI-disclosure statute for existing employees.** No Ontario or federal law in force was found that requires telling existing employees that AI is used, or gives a right to explanation or human review. The posting rule (A7) covers external applicants only. | A negative finding | [Checked] on the listed items; a negative cannot be proven | Telling leaders is still the recommended path (below). |
| B10 | **Privacy.** No general privacy statute governs this employer's employee records (PIPEDA covers employee data only in federal works; Ontario has no private-sector Act). Common-law privacy torts apply. Guest personal information **is** covered by PIPEDA. | *Jones v. Tsige*, 2012 ONCA 32; OPC guidance | [Checked] on PIPEDA and *Jones v. Tsige*; [Not checked]: that no statute restricts sending employees' work notes to an outside AI provider, or storing them outside Canada, is a negative finding that was not re-searched | A separate store, Operator-granted access, PIN re-entry (kept on the server, closed by Lock now and by signing out, asked for again after 8 hours), a dedicated session key required on the live server, an activity log, no review content in any other system, no name in a page title. Names, emails and phone numbers are removed before anything goes to the AI provider; guidance tells reviewers never to write guest details. |
| B11 | **Reviews are evidence.** A run of positive reviews undercuts a later cause allegation; a sudden negative one, or one written after a decision, reads as pretext. AI inputs and drafts are documents too. | *Brake v. PJ-M2R Restaurant Inc.*, 2017 ONCA 402; firm commentary | [Checked] | Every rating has a named person and a time; observations in a finished review are locked; the AI request and reply are kept; calibration changes need a reason, and the leader's copy says plainly that the Operator set that level (never that the reviewer chose it). The acknowledgement confirms receipt, never agreement. |
| B12 | **Dismissal for poor performance** is rarely "cause": it needs communicated standards, support, a clear warning, time to improve and continued failure. The ordinary route is without cause, on notice or pay in lieu, never for a discriminatory or reprisal reason. | Firm commentary; playbooks 2 and 3 | [Partly checked] | The tool recommends no consequence and the AI is forbidden the words. The escalation note is shown wherever a review could lead that way. |
| B13 | **Harassment includes virtual conduct.** Review text and comments count, and a harassment complaint raised in a leader's response starts the duty to investigate. | OHSA Part III.0.1 | [Checked] as to the definition | Every comment a leader adds when confirming a review is put in front of the Operator (on the review, the Leader reviews page, the People home page and the menu count) until the Operator records that it was read and followed up outside the tool; closing the cycle does not clear it. A word list marks the ones that use harassment, safety or complaint words and shows the escalation note, but it does not decide which are shown. Every reflection a leader has sent is listed for the Operator in the same places until the Operator records that it was read (the same word list marks the ones that use such words in red; it does not decide which are shown), and the reflection page tells the leader that their reviewer reads it and that a concern about how they are treated, or about their reviewer, goes to the Operator directly. The leader is never blocked. The assigned reviewer can read the comments as well, as the leader's page says. |

**Why telling leaders is the recommended path even without a statute.**
Three reasons. A review that someone later learns was partly machine-drafted
looks like concealment, and concealment is what turns an ordinary review
into evidence of bad faith (B11). The cautious reading of B1 is that any
AI step belongs in the monitoring policy the moment app activity is used.
And the brief relies on the courts' honesty-in-performance cases (*Bhasin*,
*Callow*) for the point that a review must be true to the evidence and
never a paper trail for a decision already made; those cases were not
re-checked on review, and the Supreme Court has left open whether there is
a general duty of good faith during employment (Part E). The cost of
telling leaders is one written notice.

**Retention of reviews.** No statute sets a period. The brief's suggestion
(a synthesis, not a statutory figure) is employment plus three years, and
for a leader who was under 18, the later of that and their 21st birthday.
The tool has no removal routine for reviews in version 1, which is
consistent with that.

## Part C — Ready-to-use wording

Square brackets are facts for the Operator to fill in or confirm. **Do not
send a sentence that is not true of this store.**

### C1. Notice to leaders (send before the first cycle; required before AI drafting is switched on)

> Starting [date], we are introducing a regular leader review. Here is how
> it works. Your reviewer writes down what they have observed about your
> work against our written standards, which you already have [say where].
> If we ask you for one, you add your own reflection. [An AI tool then
> helps prepare a first draft from those notes and your reflection. Your
> name and the names of people on our team are taken out before anything
> is sent to the AI service, which is run for us by an outside provider
> and may process information outside Canada.] Nothing from your Team Hub
> activity is used. [The AI does not decide anything about you.] Your
> reviewer rates each standard themselves, [checks the draft,] and a
> person makes every decision. You will see your review, talk it through
> with [reviewer or Operator], and can add your own comments, which stay
> with the review. Time you spend on your reflection and your review is
> paid time. Reviews are confidential to you, your reviewer and [Operator
> name]. [They are kept on our Team Hub server, which a hosting company
> runs for us and which may be outside Canada.] Time away on a protected leave or under an accommodation is not
> counted against you. If anything is affecting your work that you would
> like support or an accommodation for, please tell [name] at any time.
> Questions about the process or how your information is handled:
> [contact].

Leave the bracketed AI sentences out while AI drafting is off. The
Settings page carries the same notice with the AI sentences in, for the
day AI drafting is switched on; there "may process information outside
Canada" and the paid-time sentence sit in square brackets, as facts to
confirm before sending. The leader's own pages state the paid-time point
outright ("Your reflection counts as paid time", "Reading your review and
talking it through counts as paid time"), so it has to be true in
practice: see Part E.

### C2. Reviewer guidance (shown beside every box where a reviewer writes)

**Write:** what you saw or heard yourself, with the date and the shift;
which standard it is about, by name; what it meant for guests, the team or
the store; what they did well, in the same detail as what needs work; what
support was offered and what you expect next, in words someone could see
or count. Write it within a day or two. Give every leader the same level
of detail.

**Never write:** health, disability, injuries, medication or how someone
seems to be coping; pregnancy, plans for children, childcare, eldercare or
home life; religion, church, or taking part in prayer or devotionals; age,
"maturity", "energy" or school; accent, culture or where someone is from;
gender, sexuality or relationships; guesses about why something happened;
labels for the person ("lazy", "bad attitude", "not a culture fit");
sick days, approved leave or approved limits on availability; that they
raised a concern or complaint, or anything about WSIB; things you were told
but have not checked; comparisons with a named co-worker; guest names or
other people's personal details.

**If you think something personal may be behind a change in someone's
work:** stop, do not rate that standard, and talk to the Operator before
you finish. Do not ask about a diagnosis. Ask: "Is there anything you need
from us to help you meet this expectation?"

### C3. Leader acknowledgement (exactly as the tool shows it)

> I confirm that I received this review and that it was discussed with me
> on [date]. This confirms receipt and discussion only. It does not mean I
> agree with the review. I can add comments below, and they will be kept
> with this review.

Shown above it: "If anything here is affecting your work that you would
like support or an accommodation for, please tell the Operator at any
time." The print view adds signature lines and "Leader declined to sign;
copy provided on ____". No review document may contain a waiver, a release
or "I agree with this assessment".

### C4. Posting statements (printed by the tool)

- **Vacancy:** "This posting is for an existing vacancy." or "This posting
  is not for an existing vacancy. We are collecting applications for future
  openings."
- **AI (only while AI is in use for the posting):** "We use artificial
  intelligence (AI) to help assess applications for this position. The AI
  tool compares your written answers with the job-related criteria in this
  posting and suggests a score. A member of our leadership team makes every
  decision." (The brief's sentence went on to say a person "reads every
  application". The tool enforces that a person makes every decision; it
  does not check that every application was opened, so that part is not
  printed.)
- **Accommodation:** "[Employer] welcomes applications from people with
  disabilities. Accommodation is available on request at every stage of our
  hiring process, including this application and the interview. To ask for
  an accommodation, or to get this form another way, contact [name] at
  [email or phone]. You do not have to tell us about a disability to apply,
  and asking for an accommodation will not affect how your application is
  assessed."
- **Minimum age:** "Ontario law sets a minimum age of 14 to work in a
  restaurant's serving areas and 15 in its kitchen."
- **Student rate (only with the student box ticked and a lower figure
  below the general minimum wage):** when the lower figure is exactly the
  student minimum, "The lower rate is Ontario's student minimum wage, which
  applies to students under 18 who work 28 hours a week or less during the
  school year or work during a school break." When it sits between the two
  minimums, "The lower rate is a student rate. It applies to students under
  18 who work 28 hours a week or less during the school year or work
  during a school break."
- **Interview invitation adds:** "Accommodations are available on request
  for the interview and for anything used in it. If you need one, please
  tell [name] at [contact]."
- **Offer adds:** "We have policies for accommodating employees with
  disabilities. [Name] will share them with you, and you can ask for an
  accommodation at any time." **This sentence is only true if those
  policies exist in writing.** Confirm before the first offer.

Any other place the job is advertised (a job site, a social post that
names the job, a poster) has to carry the pay, vacancy, AI and
accommodation statements itself: an ad for the position is a posting in
its own right (A2), and the store stays responsible for copies placed by
others. A general "we're hiring" sign that names no position can simply
point to the careers page. Whether a link alone would be enough was not
verified (Part E).

### C5. Applicant privacy notice (printed above the consent tick)

> We collect the information on this form, and your answers to the quick
> questions that follow, to assess your application for work at
> [Employer], to contact you about it, and to meet our legal
> record-keeping duties. It is seen only by the Operator and the leaders
> involved in hiring. Our records are kept on a server that a hosting
> company runs for us, and they may be stored outside Canada. [Only while
> AI is in use for the posting: Your
> written answers to the quick questions may be sent to an AI service run
> by an outside company, which may process them outside Canada. Your name
> and contact details are not sent with them.] We do not sell your
> information or use it for marketing. If you are not hired, we keep your
> application for [24] months after the hiring decision. After that, the
> Operator removes your name, contact details, availability, experience
> and answers from our records. We keep them longer only if a complaint or
> legal claim means we must. Ontario law requires us to keep a copy of
> this posting, the blank application form and any notice we give you
> after an interview, with your name, for 3 years. You can ask to see,
> correct or withdraw your application by contacting [name and contact].
> By sending this form you confirm the information is true and agree to
> this use. If you are under 18, you may wish to read this with a parent
> or guardian.

The three-year sentence is true as written: only a notice given after an
interview keeps the applicant's name past the stated months. A notice
logged for someone who was never interviewed (the usual "not moving
forward" message) loses its wording and the name when the Operator removes
that applicant's details. An interview that was logged and never marked
counts as an interview here, so that notice is kept.

**Two promises in it are kept by a person, not by the tool:** the removal
after the stated months (the Operator presses the button on the Retention
page; the People home page shows a reminder when anyone is due), and
answering a request to see, correct or withdraw (the hiring contact; a
leader can correct name and contact details on the applicant's Contact
page and mark them withdrawn).

### C6. Decision notices to an interviewed applicant (copy-ready in the tool)

- **Offer:** "Hi [first name], thank you for interviewing for [position]
  (posted [date]). A hiring decision has been made for this posting, and
  we are pleased to offer you the position. [Name] will contact you with
  the details."
- **Not moving forward:** "Hi [first name], thank you for interviewing for
  [position] (posted [date]). A hiring decision has been made for this
  posting, and we will not be moving forward with your application. We
  appreciate the time you gave us and wish you well."
- **No decision by day 45:** "Hi [first name], thank you for interviewing
  for [position] (posted [date]). A hiring decision has not yet been made
  for this posting. We will contact you when it has."

The tool opens the leader's own email or text app with the message; it
sends nothing itself. The notice exists for the three-year record only
once the leader logs it. In the not-moving-forward message, an applicant
who was never interviewed is thanked for applying and is not told that a
hiring decision has been made; their private status page says the same
("We will not be moving forward with your application").

### C7. Draft electronic monitoring paragraph — **for later, not in use**

**Not needed for the tool as built, which uses no Team Hub activity in
reviews.** Keep it for the day that is wanted. It would have to be made
true, added to the store's electronic monitoring policy, dated, and given
to **every** employee within 30 days of the change, before any activity
data is used. Have counsel or e2r read it first (Tier 2).

> Team Hub activity and leader reviews. [Legal employer name]
> electronically monitors employees' work activity in Team Hub, our
> internal app. How and when: whenever a leader uses Team Hub, on a store
> device or a personal one, the app records [which checklists and items
> the leader signed off and when; to-dos assigned to or by the leader and
> when they were completed; progress through leadership training; and
> whether and when required announcements and required reading were
> opened]. [For all team members, Team Hub records: confirm and list.]
> [Team Hub does not record location, keystrokes, screen activity or
> anything outside the app.] Why: we use this information to run the
> store, to plan training, and as one source of factual evidence in leader
> performance reviews, coaching and, where warranted, corrective action.
> [For leader reviews, a summary of a leader's activity and the reviewer's
> written observations may be sent to an artificial intelligence (AI)
> service run for us by an outside provider, which prepares a draft for
> the reviewer. The provider may process the information outside Canada.
> The AI service does not make decisions. A person reviews every draft and
> makes every decision.] Our commitments: activity is read against the
> shifts a leader was actually scheduled to work. Time away on a protected
> leave, under an accommodation or because of a workplace injury is not
> counted against anyone. [The content of private one-on-one threads is
> not used in reviews.] Policy prepared: [date]. Last changed: [date].

Whether the store's **current** electronic monitoring policy already
describes what Team Hub records for its ordinary purposes is a separate
question and an open store fact (Part E).

## Part D — Escalation flags

Against the tiers in [escalation-matrix.md](./escalation-matrix.md). The
matrix was written for employment matters and has not been changed for
this tool. An item marked **(not in the matrix)** is this reference's own
recommendation, placed by analogy with the nearest line the matrix does
have. Until the Operator and counsel decide whether to add these to the
matrix itself, treat them as written here.

**Tier 1 — handle here**

- Posting compliance, the application form, notices, the youth wage and
  age rules; building and running both features.
- The notice to leaders, reviewer guidance and the acknowledgement.
- Routine coaching notes, written warnings and PIPs that follow a review,
  each with the consistency, pretext and protected-context checks
  (playbook 1).

**Tier 2 — draft here, verify with counsel or e2r before acting**

- **Switching on AI-assisted scoring of applicants.** Not listed in the
  matrix; recommended by analogy, because the Code exposure is systemic
  and untested.
- The AI provider's data terms, and the final applicant privacy notice
  with the retention period chosen. **(not in the matrix)**
- A leader's accommodation request heading toward "cannot accommodate",
  and, by analogy **(not in the matrix)**, an applicant's.
- A response to a Ministry of Labour complaint or inquiry about a posting,
  a 45-day notice or the records.
- Any change that ties pay, bonus, title or leadership status to review
  results (pay equity and the merit-pay rules were not verified). **(not
  in the matrix)**
- Any termination of a leader with three or more years of service; any
  package above ESA minimums; any release.
- A harassment investigation with substantiated findings against a leader.
- Offer letters or employment agreements: counsel finalises the text.
- Changing the electronic monitoring policy to cover review use (C7).
  **(not in the matrix)**
- Any decision to run police or consumer-report checks. **(not in the
  matrix)**

**Tier 3 — STOP: counsel first, before any action at all**

- Any for-cause termination, including "cause" framed as poor performance.
- Using a review toward ending the job of anyone who is pregnant, on or
  recently back from a protected leave, has a disclosed disability or an
  open accommodation file, has an open WSIB claim, or recently made a
  harassment, safety or wage complaint. **A review the tool has put on
  hold is this flag raised early**: get the risk read before releasing it
  toward any consequence.
- Refusing to hire because of a disclosed disability, an accommodation
  request, religion-linked availability or a record of offences. **(not in
  the matrix.** The matrix has no line for a hiring refusal. The hiring
  research placed this one tier lower, by analogy with accommodation
  files; this reference keeps the stricter tier, and the tool tells
  leaders that a lawyer is called first, until the Operator and counsel
  settle which tier it belongs in.**)**
- A demand letter, HRTO application, OLRB reprisal complaint or civil
  claim that cites a review, a hiring decision, the tool or the AI. Engage
  counsel the same week, put the applicant on **Hold: do not delete**, and
  keep every backup.
- A leader's review comments, or anything in hiring, alleging misconduct
  by the Operator or a senior leader.
- Anything involving a leader, applicant or new hire under 18 with a
  safety or harassment allegation.

**Standing rules.** Review content, application records, AI requests and
replies are not privileged. No acknowledgement or review document may
waive rights. Decision-critical figures need a "verify current" check.

## Part E — What could not be verified, and store facts still needed

### Not verified

1. **No primary text was read.** ontario.ca, e-Laws, ohrc.on.ca and
   canlii.org were not reachable from the build environment. Every section
   number and quotation should be checked on e-Laws before it is treated
   as settled.
2. Whether "associated application form" in the three-year rule means the
   blank form or each completed application. The tool keeps the blank form
   three years and completed applications for the Retention setting
   (default 24 months after the decision).
3. Applying the $50,000 annual cap to an hourly range: arithmetic only.
4. Whether tips are excluded from "compensation" (one firm bulletin).
5. Whether a short phone screen, a one-way video or an on-the-spot
   conversation is an "interview". The safe course is to log any live
   suitability conversation as one.
6. Whether a second notice is owed when a decision follows a "no decision
   yet" notice, and how day 45 is counted on a weekend or holiday.
7. Whether rule-based scoring, or a job board's built-in ranking, counts
   as "using AI". If in doubt, disclose.
8. Penalties for breaching the posting rules: none confirmed.
9. Code ss. 11, 17, 24 and 46.3 wording, and whether an AI vendor is the
   employer's "agent".
10. OHRC form guidance is an older edition: SIN timing and the availability
    wording were not re-confirmed.
11. IASR ss. 12, 14, 23(2) and 26 wording; what O. Reg. 69/26 (March 2026)
    changed.
12. The Education Act section and fine for employing a school-age person
    during school hours.
13. The list of items in the new-hire written information.
14. That in-app activity logs are "electronic monitoring" (an inference),
    and ESA s. 41.1.1(3)–(5) numbering.
15. The Human Rights Code and reprisal rules in Part B were not re-searched
    on review (a search limit was reached): B2, B5 in part, B6 in part.
16. The poor-performance cause test (firm commentary only); the Pay Equity
    Act and ESA equal-pay text and thresholds.
17. The HRTO time limit for minors; the PHIPA rule on health information
    received from a custodian.
18. Privacy torts and qualified privilege applied to internal reviews: no
    case on point was found.
19. The status of Ontario Bill 105 after September 2026 and of federal
    Bill C-36 (sources conflict). Neither changes the rules above.
20. The review retention suggestion in Part B is a synthesis, not a
    statutory figure.
21. Not re-searched on review in the leader-review brief (a search limit
    was reached): that no statute restricts sending employees' work notes
    to an outside AI provider or storing them outside Canada (B10); the
    constructive dismissal cases (*Potter*, *Shah*) behind the Tier 2 flag
    on tying pay, hours or title to review results; the honesty and good
    faith cases (*Bhasin*, *Callow*, *Honda*, *Matthews*); and the rules
    for leaders under 18.
22. Applicants: that no rule specifically governs sending applicant
    answers to an outside AI provider is a negative finding (A18), and
    whether PIPEDA applies to the AI provider in its own right was not
    determined.
23. CASL: the CRTC's view that recruitment messages are not commercial
    was seen only as a search extract (A19).
24. The *Consumer Reporting Act* and *Police Record Checks Reform Act*
    details behind the "not built" note on background checks.
25. Whether a link alone to the posting, from an ad for the position placed
    somewhere else, satisfies ESA Part III.1. The statements are required
    "in the posting", so the tool and this document say each ad carries
    them itself.
26. Whether AI may start being used for a position partway through, once
    the statement is added to a posting that first went out without it
    (ESA s. 8.4). The tool takes the strict reading and does not allow it:
    AI assist is switched on before a posting goes live or not at all.

### Store facts still needed

| Fact | What turns on it |
|---|---|
| Headcount on each posting date, and on 1 January | Whether the posting rules and the electronic monitoring and new-hire information duties apply (they are assumed to). |
| Whether the store has 50 or more employees | The public careers pages would have to meet WCAG 2.0 AA, and documented accommodation plans become mandatory. |
| The current electronic monitoring and disconnecting-from-work policies: whether they exist and what they say about Team Hub | B1, B7; nothing off-shift should ever be assessed. |
| Whether written accommodation policies exist | The offer message says they do (C4). |
| Whether any leader is under 18 | The Tier 3 flag for minors; retention. |
| The AI provider's data terms (training on store data, retention, storage location, deletion) | Both go-live checklists; the "may process outside Canada" sentences. |
| Which country the Team Hub server is in. When this was written the hosting service ran it in the United States, so applicant records and leader reviews are stored outside Canada whether or not AI is used | The "may be stored outside Canada" sentences in C1 and C5 (B10). If the Operator wants the records kept in Canada, the service has to be moved to a Canadian region or host first. |
| Whether reviews will affect pay | Tier 2: merit-pay and pay-equity rules. |
| Whether leaders are in fact paid for the time they spend on a reflection, on reading their review and in the review conversation. **The leader's pages tell them it counts as paid time**, and the tool does not record the time: the Operator has to confirm payroll does this before the first cycle | B7; a statement the tool makes that only practice can keep true. |
| Whether the store's standards have been read for behaviour-only wording | B4. |
| Whether leaders have enforceable termination clauses | Any exit that follows a review (playbook 2). |
| Whether the AODA compliance report due 31 December 2026 has been filed | A separate duty noted in the brief. |
