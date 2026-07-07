# CROUS Housing Notifier Bot

A GitHub Actions bot that checks CROUS housing search URLs, emails immediate listing changes, and sends one daily report per target.

## Production Targets

The production config is in `crous_targets.json` and should contain only:

| Target | Email secret | Data folder | Cities | CROUS tool |
| --- | --- | --- | --- | --- |
| Bordeaux | `TO_EMAIL` | `data/bordeaux` | Bordeaux, Pessac, Talence, Mérignac | `/tools/43` |
| Strasbourg | `FRIEND_TO_EMAIL` | `data/strasbourg` | Strasbourg, Illkirch-Graffenstaden, Schiltigheim | `/tools/43` |

Both targets use:

- `send_immediate_alert: true`
- `send_daily_report: true`

Do not commit test targets to `crous_targets.json`.

## What Gets Persisted

The bot writes several CSV files per target, but only the small files required for future runs should be committed:

| File | Committed? | Purpose |
| --- | --- | --- |
| `current_available.csv` | Yes | Latest snapshot used to detect additions and removals. |
| `daily_report_log.csv` | Yes | One-row-per-sent-report guard to avoid duplicate daily reports. |
| `run_log.csv` | No | Uncapped execution history for scrape counts, errors, and recipient audit with masked emails. |
| `availability_changes.csv` | No | Append-only add/remove history. |
| `unique_residences.csv` | No | Historical catalog of listings seen over time. |

GitHub Actions restores and saves the non-committed history files with Actions cache, and also uploads them as a short-retention workflow artifact. This keeps useful operational history available without repeatedly committing growing generated logs.

`run_log.csv` is intentionally not capped in code. It records every run, including runs with no listing changes, and masks recipient emails such as `me***@g***.com`.

## Alerts And Reports

Immediate alerts are sent when a target has added or removed listings. Subjects are clean city labels:

```text
CROUS Bordeaux: +2 / -0 logements
CROUS Strasbourg: +1 / -0 logements
```

Daily reports are sent once per target per CET day when `send_daily_report` is true. By default, the report is eligible after 23:00 CET to match the old daily-report behavior. Override with `DAILY_REPORT_HOUR_CET` if needed.

Email bodies show the useful listing detail line, for example:

```text
19 m² | Individuel | 1 lit simple | WC, Douche, Frigo, Micro-onde
```

Residence IDs are internal and should not appear in recipient-facing email content.

## CSV Columns

Generated listing CSVs use this order:

```text
residence_id, name, housing_type, price_text, price_min_eur, price_max_eur, surface_text, surface_min_m2, surface_max_m2, details, address, link, source_url, first_seen_cet, last_seen_cet
```

If a price or surface has only one number, it is written to the `min` column and the matching `max` column is left empty.

## GitHub Actions

The workflow is `.github/workflows/run_check.yml`.

It:

- supports `workflow_dispatch` for cron-job.org
- keeps a small daily schedule so GitHub does not disable the workflow
- installs `requirements.txt`
- runs `python crous_notifier.py`
- exposes `BREVO_LOGIN`, `BREVO_API_KEY`, `FROM_EMAIL`, `TO_EMAIL`, and `FRIEND_TO_EMAIL`
- caches/uploads bulky generated history files
- commits only small state files under `data/`

Required repository secrets:

| Secret | Value |
| --- | --- |
| `BREVO_LOGIN` | Brevo SMTP login. |
| `BREVO_API_KEY` | Brevo SMTP key/password. |
| `FROM_EMAIL` | Verified Brevo sender email. |
| `TO_EMAIL` | Bordeaux recipient email. |
| `FRIEND_TO_EMAIL` | Strasbourg recipient email. |

## cron-job.org

Use cron-job.org to dispatch the workflow on the cadence you want.

Request:

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

## Brevo SMTP

The bot sends through `smtp-relay.brevo.com:587`.

1. Verify the sender email or domain in Brevo.
2. Store the SMTP login in `BREVO_LOGIN`.
3. Store the SMTP key/password in `BREVO_API_KEY`.
4. Store the verified sender in `FROM_EMAIL`.
5. Store recipient emails in `TO_EMAIL` and `FRIEND_TO_EMAIL`.

Never commit real recipient emails or Brevo credentials.

## Cleanup Rules

Do not commit:

- root-level generated CSVs such as `daily_activity_log.csv`, `removed_residences.log.csv`, or `daily_report_log.csv`
- obsolete `bordeaux_data/` output from older bot versions
- `data/test_*/` folders
- empty or old test CSV files
- generated `run_log.csv`, `availability_changes.csv`, or `unique_residences.csv`

Keep only the small production state files that let the next Action run compare current CROUS availability and avoid duplicate daily reports.

## History Cleanup

This commit stops new bloat, but old generated CSVs still live in Git history until the repository history is rewritten.

Recommended cleanup with `git filter-repo`:

```bash
python -m pip install git-filter-repo
git clone --mirror https://github.com/jabrane-me/crous-bot-notifier.git crous-bot-notifier-cleanup.git
cd crous-bot-notifier-cleanup.git
git filter-repo \
  --path daily_activity_log.csv \
  --path removed_residences.log.csv \
  --path daily_report_log.csv \
  --path available_residences.csv \
  --path bordeaux_data \
  --path data/test_jabrane_main \
  --invert-paths
git push --force --mirror
```

Before force-pushing rewritten history, export any old production CSVs you still want to keep outside Git, tell collaborators to reclone, and pause cron-job.org so it does not dispatch while the rewrite is happening.

If you do not need the old history, a fresh clean repository seeded with the current source files plus the latest production `current_available.csv` and `daily_report_log.csv` files is the simplest alternative.

## Local Use

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run with recipient environment variables:

```bash
TO_EMAIL=you@example.com FRIEND_TO_EMAIL=friend@example.com python crous_notifier.py
```

Local runs may create ignored generated CSV files under `data/`.
