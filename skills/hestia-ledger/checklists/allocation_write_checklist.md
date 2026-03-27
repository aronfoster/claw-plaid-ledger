# Allocation Write Checklist

Run before `PUT /transactions/{id}/allocations`.

- [ ] I re-fetched the transaction with `GET /transactions/{id}` in this run.
- [ ] I verified date, amount, pending/posting status, and current allocations
      (`allocations[0].category`, `allocations[0].tags`, `allocations[0].note`).
- [ ] I checked `allocations.length`. If `> 1`, the transaction has been split
      by an operator — I will not silently discard existing split allocations.
      (If the intent is unclear, flag for Athena review instead of writing.)
- [ ] Proposed tags are short, factual, and normalized.
- [ ] Proposed note is evidence-based and privacy-safe.
- [ ] If confidence is low, I used `needs-athena-review` escalation tagging.
- [ ] I am using `PUT /transactions/{id}/allocations` (not `PUT /annotations/{id}`).
- [ ] I am not attempting to override canonical precedence behavior.
