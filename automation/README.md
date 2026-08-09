# VC comps automation

One self-contained nightly pipeline that **discovers new VC firms and refreshes
existing ones in a single pass**, then lets Zapier write the results to Airtable.
One service, one loop, no work done twice.

Full design, Airtable schema, Zapier wiring, and deploy steps: **[`PIPELINE.md`](PIPELINE.md)**.

## The idea in one line
A new firm is just a firm whose previous dataset is empty — so the same loop emits
"all added + a `new` registry row" for new firms and "added/dropped/exited deltas"
for existing ones. No separate discovery service, no second phase, no double dedup.

## Quick start
```bash
pip install -r automation/requirements.txt
cp automation/.env.example automation/.env    # GITHUB_TOKEN + ZAPIER_CATCH_HOOK_URL
set -a; source automation/.env; set +a
python3 automation/pipeline.py --dry-run --limit 3
```

## Secrets (minimal by design)
Railway holds only a one-repo GitHub PAT and a fire-only Zapier hook — neither can
read Airtable or Drive. The Airtable PAT lives only in Zapier and, for the one-time
bulk load, on your laptop (`backfill_airtable.py`). No Google credential anywhere.

## Files
`pipeline.py` (cron entrypoint) · `roster.py` (one deduped firm list) ·
`identity.py` (all slug/key/exit rules, defined once) · `gh.py` (own GitHub client) ·
`extract.py` (portfolio-URL finder + generic extractor) · `diff.py` (deltas + health) ·
`zapier_client.py` (fire-only hook) · `candidates.json` (discovery queue) ·
`backfill_airtable.py` (one-time local load).
