# Athena Anomaly Review Flow

1. Run canonical queries over a fixed window and complete pagination.
2. Classify findings as one or more of:
   - `spend-spike`
   - `missing-expected`
   - `possible-duplicate`
   - `category-mismatch`
   - `orphan-transaction`
   - `cross-source-discrepancy`
3. Re-fetch each candidate with `GET /transactions/{id}` before conclusions.
4. If confidence is low or evidence is partial, mark as
   `needs-athena-review` and state unresolved questions.
5. If adding clarification allocation updates, keep them low-volume and include
   explicit timeframe + follow-up action in the note.
