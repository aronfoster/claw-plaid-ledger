# Anomaly Review Template

Use this structure for anomaly-focused responses.

## Query frame

- Window: `<YYYY-MM-DD>` to `<YYYY-MM-DD>`
- View(s): `canonical` (and `raw` only if explicitly auditing discrepancies)
- Filters: `<owner/tags/include_pending>`
- Data coverage: `<full|partial + reason>`

## Findings by anomaly type

### 1) Unusual spend spikes

- Candidate transactions:
  - `<tx_id> | <date> | <merchant> | <amount> | <owner> | <status>`
- Baseline comparison used:
  - `<comparison window or peer group>`
- Assessment: `<likely spike|uncertain>`

### 2) Missing expected transactions

- Expected pattern:
  - `<merchant/amount cadence>`
- Evidence checked:
  - `<queries run + window>`
- Assessment: `<missing likely|unable to verify>`

### 3) Likely duplicates

- Candidate pairs/groups:
  - `<tx_id A> + <tx_id B> | <reason>`
- State interaction:
  - `<pending-posted overlap|posted-posted>`
- Assessment: `<likely duplicate|needs review>`

### 4) Category/tag inconsistencies

- Transactions:
  - `<tx_id> | <merchant> | <current category/tags> | <expected label>`
- Assessment: `<likely mismatch|uncertain>`

## Confidence and escalation

- Overall confidence: `<high|medium|low>`
- Uncertainty sources:
  - `<failed calls / partial pagination / pending-only evidence>`
- Required human follow-up:
  1. `<action 1>`
  2. `<action 2>`

## Optional annotation plan

- Eligible transactions for annotation:
  - `<tx_id> -> tags:[needs-athena-review, <anomaly-tag>]`
- Note format:
  - `Window <start..end>: <observed signal>. Needs human review: <next step>.`
