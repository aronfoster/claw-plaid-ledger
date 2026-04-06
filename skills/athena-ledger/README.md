# Athena Ledger Skill Bundle

This directory is a copy-ready OpenClaw skill bundle for Athena's analysis
work.

## What is in this bundle

- `SKILL.md` — analyst contract for queue review, rollups, and anomaly
  narratives.
- `checklists/query_playbooks.md` — summary/owner/tag/discrepancy playbooks.
- `checklists/anomaly_review_flow.md` — focused anomaly handling flow.
- `templates/owner_summary_template.md` — owner-aware reporting output format.
- `templates/anomaly_review_template.md` — anomaly narrative output format.

## How to copy into a new OpenClaw workspace

```bash
cp -R athena-ledger "$OPENCLAW_SKILLS_DIR/athena-ledger"
```

## Trigger modes

1. **Scheduled cadence (recommended):** run daily/weekly/monthly for spend
   rollups and anomaly review. Sync data arrives via systemd timer
   (`ledger sync --all --notify`, 4×/day by default); Athena's own cadence
   is independent and typically lower-frequency.
2. **Optional handoff mode:** run after Hestia escalates records with
   `needs-athena-review` where agent-to-agent routing is available.

## Runtime profile

- **Cadence:** periodic, not per-transaction sync.
- **Output style:** human-facing summary + anomaly interpretation.
- **Write behavior:** low-volume clarification allocation updates only.
