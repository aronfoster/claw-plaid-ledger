# Owner-Aware Summary Template

Use this structure for deterministic household summaries.

## Query frame

- Window: `<YYYY-MM-DD>` to `<YYYY-MM-DD>`
- View: `canonical` (or `raw`, if explicitly requested)
- Filters: `<owner/tags/include_pending>`

## Household rollup

- Total spend: `<amount>`
- Transaction count: `<count>`
- Pending items: `<count + note>`

## Per-owner sections

### owner:alice

- Spend total: `<amount>`
- Notable transactions:
  - `<date> | <merchant> | <amount> | <status>`
- Signals requiring review: `<none or list>`

### owner:bob

- Spend total: `<amount>`
- Notable transactions:
  - `<date> | <merchant> | <amount> | <status>`
- Signals requiring review: `<none or list>`

## Confidence and follow-up

- Confidence: `<high|medium|low>`
- Limits:
  - `<missing data / pending-only evidence / API errors>`
- Suggested next checks:
  1. `<check 1>`
  2. `<check 2>`
