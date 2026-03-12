# Annotation Write Checklist

Run before `PUT /annotations/{transaction_id}`.

- [ ] I re-fetched the transaction with `GET /transactions/{id}` in this task.
- [ ] I verified date, amount, pending/posting status, and current annotation.
- [ ] Proposed tags are short, factual, and normalized.
- [ ] Proposed note is evidence-based and privacy-safe.
- [ ] I am not attempting to override canonical precedence behavior.
- [ ] Confidence is sufficient; otherwise I abstain and report uncertainty.
