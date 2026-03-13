# Owner-Aware Summary Template

Use this structure for deterministic household summaries.

## Query frame

- Timeframe queried: `<YYYY-MM-DD>` to `<YYYY-MM-DD>`
- View: `canonical` (or `raw`, if explicitly requested)
- Filters: `<owner/tags/include_pending>`
- Coverage note: `<full pagination | partial + reason>`

## Household rollup

- Posted spend total: `<amount>`
- Pending spend total: `<amount>`
- Combined transaction count: `<count>`
- Pending-vs-posted clarification: `<how pending affected interpretation>`

## Per-owner sections

### owner:alice

- Posted spend total: `<amount>`
- Pending spend total: `<amount>`
- Notable transactions:
  - `<date> | <merchant> | <amount> | <posted|pending>`
- Signals requiring review: `<none or list>`

### owner:bob

- Posted spend total: `<amount>`
- Pending spend total: `<amount>`
- Notable transactions:
  - `<date> | <merchant> | <amount> | <posted|pending>`
- Signals requiring review: `<none or list>`

## Confidence and follow-up

- Confidence: `<high|medium|low>`
- Needs human review: `<yes/no + trigger>`
- Limits:
  - `<missing data / pending-only evidence / API errors>`
- Suggested next checks:
  1. `<check 1>`
  2. `<check 2>`
