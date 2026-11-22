# Casebook Frameworks

The frameworks every case walkthrough follows. They are taken from the user's chosen videos
(Racheal Ou's PM interview series), plus Lewis C. Lin's CIRCLES method and Jobs-to-be-Done.
Each case's walkthrough should visibly follow the steps for its case type, in this order.

---

## 1. Product Design / Improvement: CIRCLES + "List and Trim"
Source: youtube.com/watch?v=aSMo5MFdzPQ (design) and zXGKpNr-kc4 ("favourite product / improve X").
Lewis C. Lin, *Decode and Conquer*, for CIRCLES.

Interviewers look for four things: **structure, customer empathy, prioritisation and trade-offs, communication.**

| Step | What to do |
|---|---|
| **C - Comprehend the situation** | Restate the prompt. Clarify the **goal** (satisfaction? revenue? existing pain or new value?), the **context/scope** (whole journey or one step), **who am I** (the company? a vendor?) and **constraints** (money, time, team). State assumptions out loud. *Don't fish for the answer* ("Should I build an app?"). |
| **I - Identify the customer** | List several user types, then pick one, segmented by behaviour, demographic or context. Justify the pick: high value and high pain. |
| **R - Report needs** | The persona's goal, why it matters, and the pain points. Phrase needs as **JTBD**: *"When [situation], I want to [motivation], so I can [outcome]"*. |
| **C - Cut through prioritisation** | Pick 1-2 needs. Criteria: how painful, how often, whether workarounds exist, how feasible, how well it fits the business goal. |
| **L - List solutions** | At least 3 distinct ideas: **one safe, one ambitious, one visionary**. Borrow from other products, or question why things are done this way. Keep them high level. |
| **E - Evaluate trade-offs** | Place each idea on an **impact vs. effort** grid. Name edge cases and competitors. |
| **S - Summarise / recommend** | Pick one, tie it to the business goal, name the **risks and how to mitigate them**, and propose a limited rollout or test. Have **success metrics** ready, because the follow-up will ask for them. |

*List and trim*: list users, keep one. List needs, keep one. List solutions, keep one.
**Favourite-product variant**: (1) what it does, its mission and KPIs; (2) why it's your favourite: the pain it solves for you, what brings you back, and the long-term value for user and business; (3) improve it, using CIRCLES from I onwards; (4) success metrics.

---

## 2. Product Strategy: Clarify, Context (4 pillars), Recommend
Source: arTA4TFHFDw (all strategy questions) and cjqXbGMGA_Q (market entry).

**Question types**
- **Strategic plan** questions ask *what should we do*: improve, grow, long-term vision, go-to-market, monetise.
- **Strategic decision** questions ask *should we do X*: market entry, trade-offs, threats.

**Step 1: Clarify.** What success means (revenue, retention, engagement), scope (geography, segment, what "entering" means: make, sell or marketplace), and constraints (time, budget, **regulation**).

**Step 2: Analyse the context.** Combines Porter's Five Forces and the 3Cs. Spend most of the time here, but prioritise what matters for the answer.
1. **Company positioning**: mission and strategy; business model and revenue drivers; **market size / TAM** sanity check (a quick top-down estimate); strengths; weaknesses (including opportunity cost); likely future goals.
2. **Customers and pain points**: who they are, which pains are solved today, and which are still unsolved.
3. **Competitive landscape**: direct competitors and why users pick them; where they fall short; substitutes; new competitors that the move would create; trends and behaviour shifts (AI, short-form video, etc.).
4. **Market forces**: supplier bargaining power, buyer bargaining power, threat of new entrants, **cost to enter** (asset-heavy vs. platform, with rough numbers).

**Step 3: Recommend.**
- The strategy or decision, ideally a sharp *reframe* rather than yes/no. Examples: "Google shouldn't run rides; it should be the mobility infrastructure layer." "Charge businesses, keep personal chat free."
- **Why**, tied back to the analysis.
- **Risks** you accept, and how you mitigate them.
- **Execution**: build/partner/acquire, a phased rollout, and the trigger for monetising.
- For decision questions, also say *under what conditions* the answer changes, and give the alternative if it's "no".

---

## 3. Situational: SODS
Source: ofG135u4gAo.

First, list the **traits the company values** (from its careers page or JD; e.g. bias for action, customer obsession, ownership) and let them show in the answer. Don't name them as buzzwords.

| Step | What to do |
|---|---|
| **S - Scope** | Clarify the goal, the constraints (capacity, timing, uncertainty) and how success is measured. Avoid solving the wrong problem. |
| **O - Options** | 2-3 realistic paths, each with its **explicit trade-off**: speed vs. quality, short-term vs. scalable, focus vs. coverage, speed vs. confidence, momentum vs. impact. |
| **D - Decision** | Pick one and say why. Be explicit about **what you are NOT doing**. |
| **S - Success** | A hypothesis, the leading signals you'll watch, and what makes you double down, adjust or re-prioritise. |

Expect follow-ups that change a constraint ("leadership now also wants short-term revenue").

---

## 4. Root Cause Analysis: Clarify, 4 cause buckets, Hypothesise-Test-Repeat
Source: VczmHwGGBHA.

The anti-pattern is reading out a laundry list and **interrogating** the interviewer. Keep it a conversation.

**Step 1: Clarify (3 groups of questions)**
1. **The metric**: how it's defined and calculated (e.g. order *placed*, *confirmed* or *delivered*?), and what drives it.
2. **Time frame**: a sudden drop or a gradual one? Since when?
3. **Scope**: magnitude; which segments, geographies, platforms (Android/iOS/web), app versions, product areas.

**Step 2: Root-cause buckets (go in this order)**
1. **Data / instrumentation**: tracking misfire, broken event schema, pipeline or dashboard error. Always rule this out first (it's rarely the answer).
2. **Internal factors**: map the **user funnel** and find the drop-off step. Then look at product changes (UI redesign, cannibalisation, A/B tests, **pricing/fee changes**, a release or bug), company changes (reorg, acquisition, support changes), and traffic or marketing channel shifts (a paused campaign, a lost partnership).
3. **External factors**: regulation and policy (in India: **RBI, NPCI, SEBI**, state gig-work laws); seasonality (festivals, monsoon, IPL, exams, salary-day cycles); competitor moves or new entrants; **dependency outages** (UPI/bank downtime, AWS, SMS/OTP gateways, map APIs); press and social-media backlash.
4. **Consumer behaviour**: changed preferences, fatigue with the experience, choice overload.

Causes can **combine**. A small UI change plus a competitor promotion can add up to a large drop.

**Step 3: Hypothesise, test, repeat.** Ask *wide* questions that eliminate half the board, like playing Guess Who ("Has traffic been stable?" rather than "Was there a bug in checkout?"). After each answer, **say what it implies** and use it to form the next hypothesis.

**Step 4: Land it.** Name the root cause. **Validate it** with 2-3 metrics you would check (before/after, affected vs. unaffected cohorts). If time allows, suggest a **fix and a way to prevent it happening again**.

---

## 5. Success Metrics: Company, Users & value, North Star + levers, Guardrails
Source: r79tLZB2S_Y.

The anti-pattern is a generic list (DAU, revenue, retention).

1. **Company and product**: what the company is, what stage it's at, what the product's goal is, and how it serves the company goal.
2. **Users and value**: every side of the marketplace (customers, merchants, delivery partners). Walk each side's journey: **activation, engagement, conversion, retention**. Then say how the company makes money. Confirm your understanding with the interviewer.
3. **North Star Metric**: the single action where **user value meets business growth** (e.g. completed orders for Uber Eats). **Break it into a formula** of its levers (e.g. customers ordering x restaurant fulfilment rate x courier availability x efficiency). Pick the levers that matter most as **supporting metrics** (e.g. delivery time trend, repeat order rate, gross margin per order).
4. **Guardrail / counter-metrics**: make sure that pushing the NSM doesn't harm another part of the business. Example: Shorts watch time against long-form watch time. Example: completed orders against delivery time and courier utilisation. Sometimes this is just a sharper definition of the NSM.

---

## 6. Guesstimates: Clarify, Pick framework, Constraints/MECE, Calculate, Sanity check
Source: 8c0lRXbAvX0.

Interviewers look for: breaking the problem down, **business-informed assumptions** (e.g. "people upgrade phones every 2-3 years", not "every year"), the **key drivers only** (segment by what actually drives behaviour, e.g. income, not job title), clear maths, and composure.

**Step 1: Clarify.** Scope, time frame, the user definition, the metric (count or value), and how much detail is wanted.

**Step 2: Pick one of five frameworks**, whichever needs the fewest assumptions.
| Framework | Best for | Shape |
|---|---|---|
| **Top-down** | Market size or adoption in a region | Population, then filters, then target group |
| **Bottom-up** | Transactions, consumption, revenue | One unit's activity x number of units x time |
| **Funnel** | Signups, purchases, conversions | Top of funnel x the conversion rate at each step |
| **Frequency x penetration** | Repeated actions (posts, searches, orders) | Users x % who do it x how often |
| **Fermi decomposition** | Curveballs | Chain simple sub-estimates |

**Step 3: Make it stronger** with **natural constraints** (capacity, shift hours, vehicle load, store throughput) and **MECE segmentation** (groups that don't overlap and cover the whole). In India, typical segments are urban/rural, Tier 1/2/3, income class, two-wheeler vs. car.

**Step 4: Calculate** with rounded numbers. Plan for 30 seconds before you start.

**Step 5: Sanity check** with one of:
- **1-in-N test**: "1 in 66 people get a haircut today? Too high."
- **Peak-day stress test**: compare against Diwali or Big Billion Days.
- **Ratio test**: "more phones sold than people?"

Then fix the assumption that caused any error.

**India anchors** (quote these with an as-of date): population ~1.45 bn; ~300 mn households; urban share ~36%; UPI ~20 bn+ transactions a month; smartphone users ~700 mn+.
