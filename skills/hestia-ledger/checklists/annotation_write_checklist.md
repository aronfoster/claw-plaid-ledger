# Annotation Write Checklist

Run before `PUT /annotations/{transaction_id}`.

- [ ] I re-fetched the transaction with `GET /transactions/{id}` in this run.
- [ ] I verified date, amount, pending/posting status, and current allocation
      (`allocation.category`, `allocation.tags`, `allocation.note`).
- [ ] Proposed tags are short, factual, and normalized.
- [ ] Proposed note is evidence-based and privacy-safe.
- [ ] If confidence is low, I used `needs-athena-review` escalation tagging.
- [ ] I am not attempting to override canonical precedence behavior.
