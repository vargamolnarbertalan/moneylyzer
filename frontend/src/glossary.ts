// Plain-language explanations behind the ⓘ buttons (components/Explain.tsx): what a term, chart or section
// means, one worked example, and optionally how Moneylyzer works it out. Written for someone who has never read
// a finance book: short sentences, no jargon inside the explanation, numbers in the app's own format (decimal
// comma). Educational only, never advice: no "should", no good/bad verdicts on the user's figures.
//
// "~" inside a number is a non-breaking space ("1~000~000 Ft"), so a popover never wraps a number in two.
// Keep each "how" true to the code that computes the figure; drop it rather than simplify it into something false.

export interface GlossaryEntry {
  /** The term as the screen shows it. */
  title: string;
  /** A chart or a whole section rather than a term: the button is then named "About this chart: …". */
  kind?: "chart" | "section";
  /** What it is, in one or two plain sentences. */
  what: string;
  /** One concrete example with numbers. */
  example: string;
  /** Optional: how Moneylyzer works it out (shown as a separate, quieter section). */
  how?: string;
}

const volatilityBase = {
  title: "Volatility",
  what: "How much the value typically swings up and down. Higher means a bumpier ride, in both directions.",
  example:
    "Volatility 20%: in roughly two years out of three, the yearly result lands within 20% above or below its average. " +
    "A world stock fund is often around 15–20%; single shares are usually higher.",
};

export const GLOSSARY = {
  // ---- Overview
  valueChart: {
    title: "Value over time",
    kind: "chart",
    what: "What everything you hold was worth on each day, in your reporting currency.",
    example:
      "If the line goes from 1~000~000 Ft to 1~100~000 Ft in a month, your holdings are worth 100~000 Ft more. " +
      "Part of that can be new money you put in, not only price gains.",
    how:
      "At each point (a day, or an hour in the short ranges): what your holdings were worth then, at that moment's prices and exchange rates. " +
      "A purchase makes the line step up, a sale makes it step down. A holding without recorded transactions counts with today's units " +
      "throughout. Minute and hour steps use prices as traded (not adjusted for dividends), fetched when you look; funds count at their " +
      "latest daily price.",
  },
  unrealizedPL: {
    title: "Unrealized P/L",
    what: "Profit or loss (P/L) on what you still hold: what it is worth now minus what it cost you. It is \"unrealized\" because you haven't sold, so it can still change.",
    example: "You bought 10 shares at 100 and they now trade at 120: unrealized P/L is +200, or +20%.",
    how: "Today's value minus what the units you still hold cost. Sales use up your oldest purchases first.",
  },
  realizedPL: {
    title: "Realized P/L",
    what: "Profit or loss you have locked in by selling.",
    example: "You bought 10 shares at 100 and sold 4 of them at 130: realized P/L is 4 × 30 = +120. The other 6 are still unrealized.",
    how: "Each sale uses up your oldest purchases first: what the sale brought in minus what those units cost, each converted at its own day's exchange rate.",
  },
  xirr: {
    title: "Return (XIRR)",
    what:
      "How much your money grew, counting when and how much you put in or took out. Once your money has been invested for a year or more " +
      "on average, it is shown as a rate per year.",
    example:
      "You put in 1~000 two years ago and another 1~000 a year ago, and now have 2~300. That is 15% profit in total, but spread over " +
      "the time each 1~000 was invested it is about 9,7% a year.",
    how:
      "From your purchases, sales and today's value, in the reporting currency. If your money has been in for less than about a year " +
      "on average, it shows the plain return for that period instead (\"not annualised\"): +3% in two weeks would otherwise read as +116% a year.",
  },
  allocation: {
    title: "Allocation",
    kind: "chart",
    what: "How your money is split: what share of the total each holding, currency or kind of asset takes.",
    example: "If 600~000 Ft of your 1~000~000 Ft is in one fund, that fund is 60% of your allocation.",
  },

  // ---- Holdings and the holding page
  avgCost: {
    title: "Average cost",
    what: "The average price you paid per unit for what you still hold.",
    example:
      "You bought 10 at 100 and later 10 at 120: your average cost is 110. If the price is now 115, a unit is worth more than you paid " +
      "for it on average, even though the second purchase is down.",
    how:
      "In the holding's own currency. Sales use up your oldest purchases first, so after a sale the average covers only the units you " +
      "still hold. Your P/L can still differ: it also counts fees and exchange-rate moves.",
  },
  priceChart: {
    title: "Price per unit",
    kind: "chart",
    what:
      "The price of one unit of this holding over time, in its own currency. The dashed line is your average cost: above it, a unit is " +
      "worth more than you paid for it on average.",
    example:
      "If the line rises from 50 to 60, one unit gained 20%. A dollar share can still show a loss in forints when the dollar fell more " +
      "than the price rose.",
    how:
      "Daily closing prices from Yahoo Finance, a fund's published price, or prices you imported. Yahoo's are adjusted for dividends and " +
      "splits, so a 2-for-1 split doesn't show as a 50% drop. Minute and hour steps are prices as traded.",
  },
  signal: {
    title: "Trend signals",
    kind: "section",
    what:
      "A quick, mechanical read of the price trend from five simple rules. It looks only at the price chart, not at the company, " +
      "its value or your situation.",
    example: "If the price is above its 200-day average and has risen over the last 1 and 3 months, more rules are met and the score is higher.",
    how: "Score = how many of the 5 rules are met: 4–5 \"Buy candidate\", 3 \"Watch\", 2 \"Hold\", 0–1 \"Sell / review\". Needs 200 days of prices.",
  },
  rsi: {
    title: "RSI (14)",
    what: "Relative Strength Index: a 0–100 gauge comparing the size of the price's recent gains with its recent losses, over the last 14 trading days.",
    example:
      "RSI 75: the gains clearly outweighed the losses lately, often called \"overbought\". RSI 25: the losses outweighed the gains, " +
      "\"oversold\". Around 50: balanced.",
    how: "The trend signal treats 35–70 as a normal range.",
  },
  movingAverage: {
    title: "SMA 50 / SMA 200",
    what: "Simple moving average: the average closing price of the last 50 (or 200) trading days. It smooths out daily noise to show the trend.",
    example: "A price of 110 with a 200-day average of 100 means the price is above its longer-term trend.",
    how: "When the 50-day average is above the 200-day one, the recent trend is stronger than the long one: one of the trend signal's rules.",
  },
  drawdownNow: {
    title: "Drawdown from peak",
    what: "How far today's price is below the highest price stored for this holding. 0% means it is at its high.",
    example: "Highest price 200, price today 150: drawdown −25%. It would need to rise 33% to get back to 200.",
  },
  holdingVolatility: {
    ...volatilityBase,
    title: "Volatility (annualised)",
    how: "From the last 3 months of this holding's daily price moves, scaled to a year.",
  },
  scenarios: {
    title: "Scenario ranges",
    kind: "section",
    what:
      "A range of where the price could end up, built by stringing together randomly picked days from this holding's own past, " +
      "thousands of times. Not a prediction.",
    example: "Low 90, median 105, high 120 for 3 months: in 8 of 10 simulated paths the price ended between 90 and 120.",
    how:
      "2~500 paths, each from days drawn at random (a day can be drawn more than once) out of up to the last 3 years of daily moves. " +
      "10% of the paths ended below Low (p10), 10% above High (p90), half below the median.",
  },

  // ---- Risk
  riskHistory: {
    title: "How today's mix would have moved",
    kind: "section",
    what: "What you hold today, replayed over the last 1 or 3 years: how bumpy it would have been and how deep the falls went.",
    example: "Even if you bought your shares last month, this shows how those same shares behaved over the past year.",
    how:
      "Today's weights on each holding's stored daily prices, in your reporting currency, as if rebalanced to those weights every day, " +
      "so your past purchases and sales don't change the picture. Past behaviour, not a forecast.",
  },
  volatility: {
    ...volatilityBase,
    how:
      "From the daily moves over the chosen range, scaled to a year. Holdings on exchanges that close hours apart (Budapest, Frankfurt, " +
      "New York) look a little less in step day by day than over a week, so for such a mix it reads somewhat low.",
  },
  maxDrawdown: {
    title: "Largest fall",
    what: "The biggest drop from a high point to a later low point in the range.",
    example: "From 1~000~000 Ft down to 750~000 Ft before climbing back: largest fall −25%. Getting back from −25% takes +33%.",
  },
  drawdownChart: {
    title: "Below its last high",
    kind: "chart",
    what: "For each day, how far the value was below its highest point so far. At 0% it was at a new high; the deeper the dip, the bigger the fall.",
    example: "A dip to −10% in March that climbs back to 0% in June: it took three months to recover.",
  },
  beta: {
    title: "Beta and correlation",
    what:
      "Beta: how strongly your mix moved along with the world's stock markets (MSCI World). Correlation, shown next to it, says how " +
      "reliably they moved together: 1 = always together, 0 = unrelated.",
    example:
      "Beta 0,8: when world stocks rose or fell 1%, your mix moved about 0,8% on average. Beta 1,2: about 1,2%, bigger swings than the market. " +
      "With a low correlation, beta says little.",
    how:
      "From weekly moves, in your reporting currency: MSCI World is priced in Amsterdam, hours before New York closes, so daily moves " +
      "would line up badly. Only the weeks with stored MSCI World prices count.",
  },
  effectiveHoldings: {
    title: "Effective holdings",
    what: "How many equal-sized holdings your portfolio behaves like. It shows whether your money is really spread out.",
    example: "10 holdings of 10% each: 10. One at 90% and nine at about 1% each: about 1,2, so almost everything rides on one holding.",
  },
  exposure: {
    title: "Exposure",
    what: "Which currencies and kinds of assets your money is in.",
    example:
      "A US share priced in dollars moves with the dollar: if the dollar weakens 5% against the forint, its value in forints drops " +
      "about 5% even if the share price doesn't move.",
    how:
      "Grouped by the currency each holding is priced in. A fund priced in forints counts as forints here, even if it owns foreign " +
      "shares whose currencies still move its value.",
  },
  shareOfRisk: {
    title: "Share of risk",
    what:
      "How much of your portfolio's ups and downs come from each holding. The shares add up to 100%. A holding that tends to move " +
      "against the others can show a negative share: it calms the whole down.",
    how: "From daily moves, so with exchanges that close hours apart the US holdings' share reads somewhat low.",
    example: "A holding that is 20% of your money but swings a lot can cause half of the swings, while a calm holding at 50% causes far less.",
  },

  // ---- Markets
  benchmarks: {
    title: "Benchmarks",
    kind: "section",
    what:
      "Reference prices for the wider market: stock indexes, interest rates, gold, oil and currencies. They help you see whether a move " +
      "in your holdings is about them or about the whole market.",
    example: "If your shares fell 3% this week and the S&P 500 fell 3% too, it was mostly the market, not your picks.",
  },
  msciWorld: {
    title: "MSCI World",
    what:
      "A stock index of well over a thousand large and mid-sized companies in developed countries (the US, Europe, Japan and others): " +
      "a common yardstick for \"the world's stock market\".",
    example: "IWDA is a fund that follows it: if IWDA rose 10% this year, world stocks broadly rose about 10%.",
    how: "The Risk page measures beta against it.",
  },
  vix: {
    title: "VIX",
    what: "The \"fear index\": how big a swing traders expect in the US stock market over the next month, worked out from option prices.",
    example: "VIX around 12–15: calm markets. Above 30: traders expect large swings, usually during a sell-off.",
  },
  yield10y: {
    title: "US 10-year yield",
    what: "The yearly interest the US government pays to borrow for 10 years. Loans, mortgages and bond prices around the world take their cue from it.",
    example:
      "When it rises from 4,0% to 4,5% (+0,5 percentage points), bonds bought earlier lose value, because new ones pay more; shares often feel the pressure too.",
  },
} satisfies Record<string, GlossaryEntry>;

export type TermId = keyof typeof GLOSSARY;
