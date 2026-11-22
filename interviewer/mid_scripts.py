"""Mid-level scripted candidates for calibration: the band between the casebook walkthrough (strong)
and the scripted failures in simulate.WEAK.

  a = structured but shallow: sensible clarifying, a visible plan, lands an answer, but misses one major
      branch (and is a bit generic). Target: Lean hire to Hire.
  b = decent content but needs the interviewer: two '/hint' lines (a nudge, then a redirect). At the PM
      bar a redirect caps the verdict at Lean hire. Target: Lean hire.

The last line of each script answers whichever twist the interviewer picks, so it stays generic.
"""

MID = {
    "A1": {
        "a": [
            "Before I dig in, how are we defining orders here, placed or delivered? And was the drop sudden or gradual?",
            "Got it, so it's sudden from Monday. I'll split the problem by where it's happening. Is it the same on Android, iOS and web?",
            "Android is down a lot more than iOS, so something platform-specific is likely. Did anything ship last week, like an app release or an A/B test?",
            "So Android 4.98 went out to 100% on Friday and the drop starts Monday. That timing lines up. I'd check the funnel on Android, where are users dropping off?",
            "Can I see payment success by platform and method?",
            "Payment success on Android Swiggy UPI fell a lot, so users are failing at checkout after the release made it the default.",
            "My recommendation: the root cause is the Android 4.98 release that made Swiggy UPI the default, which is failing for many users. I'd roll back the default payment method on Android right away and then fix the plugin.",
            "I'd still protect users first, so I'd keep a fallback payment option on by default and fix the root issue before re-launching.",
        ],
        "b": [
            "Let me first make sure I understand the metric. Are these orders placed or delivered, and is the 20% week over week against a stable baseline?",
            "And was it sudden or gradual?",
            "Okay, sudden. First I'd rule out a data issue. Can we reconcile the dashboard count with payments and restaurant confirmations?",
            "Data looks fine then. Next I'd look at the funnel: app opens, menu, cart, payment, order. Where is the drop?",
            "/hint",
            "Right, I should cut by segment. Is it the same on Android and iOS?",
            "/hint",
            "Okay, so I should also think about the calendar. What week is this, anything like a festival?",
            "So it's Diwali week, which explains some of it, and Android is down more than iOS. Did anything ship on Android?",
            "To wrap up: two causes. Most of it is the Android 4.98 release making Swiggy UPI the default, which hurts payment success, and part of it is normal Diwali seasonality, which we see on iOS too. I'd roll back the default and add an alert on payment success by platform.",
            "I'd keep users first: give them a working payment fallback while the team fixes the plugin, and measure payment success daily.",
        ],
    },
    "B1": {
        "a": [
            "Let me clarify the goal. Is it fewer complaints and refunds, or trust and repeat orders?",
            "Okay, trust and repeat rate. Users I can think of are students, office workers and families. I'll focus on families ordering for groups, because they spend more.",
            "Their pain is that they can't tell how much food they're getting, so they order too little or feel cheated.",
            "Solution: show the portion size on the menu, like 'serves 2' and the weight in grams, for every dish.",
            "This is high impact and fairly low effort, since it's mostly a menu field change.",
            "My recommendation is to launch portion size disclosure across Swiggy, and measure success with portion complaints per 1,000 orders and repeat rate.",
            "I'd work with restaurants to fill it in, maybe start with the biggest chains and make it easy for them.",
        ],
        "b": [
            "Before solutioning, what's the goal here, fewer refunds or trust and repeat rate? And am I Swiggy, able to set menu requirements?",
            "Got it. I'll look at segments: solo value-seekers, families ordering to share, and fitness-focused users. Which categories and segments complain most?",
            "Then I'll pick families and groups: they're a third of portion complaints and they spend a lot per order.",
            "/hint",
            "Right, I should state the pain points more precisely. When a parent orders dinner for four, they can't tell how many people a dish feeds, the photos look bigger than reality, and refunds feel random.",
            "/hint",
            "Okay, ideas. Safe: mandatory 'serves N' plus grams for biryani, thali and curries. Bolder: a post-delivery portion rating that feeds a portion accuracy score in ranking. Visionary: a 'feeding how many?' cart planner.",
            "My recommendation is to start with the standard portion fields for the top complaint categories plus a one-tap portion rating, and test it in two metros. Success is portion complaints per 1,000 orders, with restaurant churn and conversion as guardrails.",
            "I'd make it worth it for restaurants with a ranking boost and pre-filled values, and phase it in gradually so we don't hurt order values.",
        ],
    },
    "C1": {
        "a": [
            "What does winning look like here, share, growth or profit, and over what time frame?",
            "Okay, protect quick-meal share in the top metros for 12 months without hurting EBITDA. Which competitor is actually hurting us?",
            "So Bengaluru share is down. I'll look at the customer: people want quick, cheap meals and snacks.",
            "Competitors: Blinkit Bistro and Zepto Cafe have their own kitchens, Ownly charges zero commission. Swiggy has Bolt and Instamart.",
            "Options: one, expand Bolt with more restaurants and promote it on the home screen. Two, launch our own kitchens. Three, give discounts to win back users.",
            "My recommendation is to expand Bolt and push it harder on the home screen, because we already have the restaurant base and it doesn't need capex like owned kitchens did with Snacc.",
            "I'd adapt but keep the core: Bolt is still our best asset, so I'd double down on it and watch share city by city.",
        ],
        "b": [
            "Let me pin down success first: is it share, GOV or profit, and over what horizon? And is the restaurant side in scope?",
            "Okay, share in the top 8 metros over 12 months, EBITDA above 3% of GOV, and restaurants in scope. Where are we losing share?",
            "Bengaluru is down about 3 points. Where did the lost orders go?",
            "/hint",
            "Good point, I should look at why they left, not just where. If price is the main reason, that's a restaurant economics problem, since Ownly's zero commission lets restaurants price lower.",
            "/hint",
            "Then options: a flat-fee plan for value restaurants to match Ownly's pricing, making Bolt the default for snack occasions, or owned kitchens. Owned kitchens lost money with Snacc, so I'd keep that as a small partner-run test.",
            "My recommendation: defend supply with a flat-fee plan for value restaurants in Bengaluru, funded partly by ads, and push Bolt for snack occasions. The trade-off is lower take rate in the short term, which I'd cap and measure against share regained.",
            "I'd keep the plan but adjust the order: if one lever is blocked, I'd lean harder on the others and set a clear checkpoint in three months.",
        ],
    },
    "D1": {
        "a": [
            "What's the feature and what sale goal does it serve?",
            "Why is it slipping, scope, a dependency, or quality?",
            "Okay, so alerts and wishlist are done and the Rs 99 lock depends on payments. My options: delay the whole thing, ship it all with more engineers, or ship part of it.",
            "I'd ship the alerts and wishlist now and move the Rs 99 lock to a later sale, because the alerts are ready and they drive most of the conversion.",
            "My decision is to cut scope: launch wishlist and alerts for BBD and move the lock to Big Diwali. I'd tell the category head and marketing about the change.",
            "I'd still stick with the scope cut and update everyone involved quickly.",
        ],
        "b": [
            "First, what is the feature, and how will leadership judge success, the launch or a sale metric?",
            "And why is it slipping, and what are the hard dates like code freeze and load test?",
            "/hint",
            "Right, I should check what was promised outside the team. Has anything been promised to brands or in the marketing campaign?",
            "So two brands planned banners and the film mentions it. Options: full scope with extra engineers, which risks the load test; a scope cut, alerts and wishlist only; or moving everything to Big Diwali, which is safe but gives up the lift.",
            "/hint",
            "Okay, using flags: I can ship alerts behind a flag to Plus and Black members in early access first, then widen on sale day if guardrails hold.",
            "My decision: cut scope. Ship wishlist and price-drop alerts behind a flag for early access, move the Rs 99 lock to Big Diwali, and tell the brands and marketing this week so the banners and film line are changed. I'm not adding engineers to payments two weeks out.",
            "I'd keep the same principle: protect the sale-day experience first, update stakeholders honestly, and keep a kill switch ready.",
        ],
    },
    "E1": {
        "a": [
            "Is this north star for the whole Instamart business, and what's the goal right now, growth or profitability?",
            "Okay, profitable growth across both surfaces. Users want groceries fast and complete. Instamart makes money on margin and delivery fees.",
            "My north star would be weekly orders, because it captures both demand and habit and every team can move it.",
            "It breaks down into active customers times orders per customer. Levers are new customer acquisition, assortment, and delivery speed.",
            "As a guardrail I'd watch contribution margin per order so we don't grow by burning cash.",
            "To summarise, my north star is weekly orders, broken into customers times frequency, with contribution margin per order as the guardrail.",
            "I'd keep weekly orders as the north star but add the new concern as a guardrail and review it monthly.",
        ],
        "b": [
            "Is this for the whole Instamart business or one surface, and what's the goal, growth, profit, or both?",
            "Got it, profitable growth. Customers want fast, complete orders; the business needs margin per order to turn positive.",
            "/hint",
            "Right, I should think about quality, not just volume. Can I measure order quality, like missing items, late delivery and refunds?",
            "Then my north star would be weekly perfect orders: orders delivered on time, complete, with no refund or complaint. It joins customer value and good unit economics.",
            "/hint",
            "Breaking it down: weekly perfect orders equals weekly active customers times orders per customer times the perfect-order rate. Levers are assortment, picking accuracy and ETA reliability.",
            "Guardrails: contribution margin per order, so we don't buy perfection with cost, and customer complaints and refunds per thousand orders.",
            "To summarise: weekly perfect orders as the north star, broken into customers, frequency and perfect-order rate, with contribution margin and refunds as guardrails.",
            "I'd keep perfect orders but check whether we're gaming it, and look at retention by cohort before deciding.",
        ],
    },
    "F1": {
        "a": [
            "Do we count only customer queries, or partners too? And is this a normal day?",
            "Okay, customers on a normal weekday, all businesses. I'll go from orders: queries equal orders per day times the share of orders with a support contact, plus non-order queries.",
            "Swiggy does about 50 lakh orders a day across food and Instamart, say. I'd assume about 6% of orders lead to a contact.",
            "So 50 lakh x 6% = 3 lakh order-linked queries a day.",
            "Then non-order queries like coupons and Swiggy One billing, say another 20%, so about 60,000.",
            "My final estimate is about 3.6 lakh customer support queries a day.",
            "I'd redo the main step with the new number and see how much the total moves, the structure stays the same.",
        ],
        "b": [
            "Just to clarify, do bot-handled queries count, and do we count conversations or messages?",
            "Got it, conversations, including bots, normal weekday, all businesses. I'll go from the order base. What's the order base?",
            "So roughly 45 lakh food and Instamart orders a day combined. I'd assume around 7% of orders trigger at least one support contact.",
            "/hint",
            "Right, I should segment by issue: delays, missing items, payments and refunds, since they have different rates. Overall still about 7%, so 45 lakh x 7% = 3.15 lakh order-linked contacts.",
            "Plus non-order queries like coupons and billing, maybe 25% on top, about 80,000, so roughly 4 lakh in total.",
            "/hint",
            "Sanity check: 4 lakh a day out of 45 lakh orders is about 1 contact per 11 orders, which feels plausible for food delivery. With 70% handled by the bot, about 1.2 lakh reach humans.",
            "My final estimate is about 4 lakh customer queries a day, with about 1.2 lakh reaching human agents, and maybe 2 to 3 times that on a heavy rain day.",
            "I'd rerun the same structure with the new assumption; the order-linked part changes most, the non-order part much less.",
        ],
    },
}
