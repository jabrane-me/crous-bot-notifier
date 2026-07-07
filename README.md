# CROUS Housing Notifier Bot

GitHub Actions bot for monitoring CROUS housing search pages. It stores per-target CSV state, sends immediate email alerts for listing changes, and sends one daily report per target during a configured time window.

## Production Configuration

`crous_targets.json` contains the production targets:

| Target | Email secret | Data folder | Cities | Search |
| --- | --- | --- | --- | --- |
| Bordeaux | `TO_EMAIL` | `data/bordeaux` | Bordeaux, Pessac, Talence, Mérignac | `/tools/43` bounds search |
| Strasbourg | `FRIEND_TO_EMAIL` | `data/strasbourg` | Strasbourg, Illkirch-Graffenstaden, Schiltigheim | `/tools/43` bounds search |

Each target enables immediate alerts and daily reports:

```json
{
  "send_immediate_alert": true,
  "send_daily_report": true,
  "daily_report_time_window": {
    "start": "23:30",
    "end": "00:00"
  }
}
```

The report window is configured per target, so recipients can use different daily report times.

## Persisted CSV State

CSV files inside each target folder are bot state and should be committed:

| File | Purpose |
| --- | --- |
| `current_available.csv` | Latest snapshot used to detect additions and removals. |
| `availability_changes.csv` | Add/remove event history for the target. |
| `unique_residences.csv` | Historical catalog of listings seen for the target. |
| `daily_report_log.csv` | Minimal daily report marker: `sent_date,sent_time_cet`. |

Root-level generated CSV files from old versions should not be committed. The old empty `data/test_jabrane_main/` output is also ignored.

## Alerts And Reports

Immediate alert subjects use clean target labels:

```text
CROUS Bordeaux: +2 / -0 logements
CROUS Strasbourg: +1 / -0 logements
```

Daily reports are sent once per target per CET day when:

- `send_daily_report` is `true`
- the current CET time is inside that target's `daily_report_time_window`
- `daily_report_log.csv` does not already contain today's `sent_date`

For the default window, reports are eligible from 23:30 up to, but not including, 00:00 CET.

The workflow uses GitHub Actions `concurrency` so frequent cron-job.org dispatches queue instead of overlapping. Each queued run checks out the latest committed target CSVs before deciding whether a daily report has already been sent.

## CSV Columns

Generated listing CSVs use this column order:

```text
residence_id, name, housing_type, price_text, price_min_eur, price_max_eur, surface_text, surface_min_m2, surface_max_m2, details, address, link, source_url, first_seen_cet, last_seen_cet
```

If a price or surface has only one number, the value is written to the `min` column and the matching `max` column stays empty.

## GitHub Actions

The workflow is `.github/workflows/run_check.yml`.

It:

- supports `workflow_dispatch` for cron-job.org
- keeps a small daily schedule so GitHub does not disable the workflow
- uses shallow checkout to avoid fetching the full bloated history on every run
- installs `requirements.txt`
- exposes `BREVO_LOGIN`, `BREVO_API_KEY`, `FROM_EMAIL`, `TO_EMAIL`, and `FRIEND_TO_EMAIL`
- runs `python crous_notifier.py`
- commits updated target-folder CSVs under `data/`

Required repository secrets:

| Secret | Value |
| --- | --- |
| `BREVO_LOGIN` | Brevo SMTP login. |
| `BREVO_API_KEY` | Brevo SMTP key/password. |
| `FROM_EMAIL` | Verified Brevo sender email. |
| `TO_EMAIL` | Bordeaux recipient email. |
| `FRIEND_TO_EMAIL` | Strasbourg recipient email. |

## cron-job.org

Dispatch the workflow with:

```text
POST https://api.github.com/repos/jabrane-me/crous-bot-notifier/actions/workflows/run_check.yml/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Body:

```json
{"ref":"main"}
```

Use a fine-grained GitHub token scoped to this repository with Actions read/write permission.

## Cleanup Rules

Keep:

- source files
- workflow and config files
- useful per-target CSV state under `data/`
- existing useful Bordeaux history under `bordeaux_data/`

Do not commit:

- root-level generated CSVs
- old empty test output under `data/test_jabrane_main/`
- temporary local files, caches, or credentials

## History Cleanup

The current tree prevents new root-level CSV bloat. Old large CSVs still remain in Git history until history is rewritten.

Recommended `git filter-repo` cleanup:

```bash
python -m pip install git-filter-repo
git clone --mirror https://github.com/jabrane-me/crous-bot-notifier.git crous-bot-notifier-cleanup.git
cd crous-bot-notifier-cleanup.git
git filter-repo \
  --path daily_activity_log.csv \
  --path removed_residences.log.csv \
  --path daily_report_log.csv \
  --path available_residences.csv \
  --path data/test_jabrane_main \
  --invert-paths
git push --force --mirror
```

Pause cron-job.org before a force-push history rewrite and make a backup of any historical CSVs that should be retained outside Git history.
