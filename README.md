# CROUS Housing Notifier Bot

A GitHub Actions bot that checks CROUS housing search URLs, emails immediate listing changes, and can send one daily report per target.

## Current Config

`crous_targets.json` is currently set to the test target:

| Target | Email secret | Data folder | URL | Daily report |
| --- | --- | --- | --- | --- |
| `TEST - Jabrane main CROUS page` | `TO_EMAIL` | `data/test_jabrane_main_email` | `https://trouverunlogement.lescrous.fr/tools/42/search` | Off, window `23->00` |

This is intentional test mode. Switch the JSON back to Bordeaux/Strasbourg before using the bot for production monitoring.

## Production Targets

The production setup is:

| Target | Email secret | Data folder | Cities | CROUS tool |
| --- | --- | --- | --- | --- |
| Bordeaux | `TO_EMAIL` | `data/bordeaux` | Bordeaux, Pessac, Talence, Mérignac | `/tools/43` |
| Strasbourg | `FRIEND_TO_EMAIL` | `data/strasbourg` | Strasbourg, Illkirch-Graffenstaden, Schiltigheim | `/tools/43` |

Production targets should use:

- `send_immediate_alert: true`
- `send_daily_report: true`
- `daily_report_time_window: "23->00"` or another per-target window

## What Gets Persisted

Per-target CSVs are part of the bot state and should be committed inside each target folder:

| File | Committed? | Purpose |
| --- | --- | --- |
| `current_available.csv` | Yes | Latest snapshot used to detect additions/removals. |
| `daily_report_log.csv` | Yes | Minimal daily report marker with `sent_date,sent_time_cet`. |
| `availability_changes.csv` | Yes | Add/remove history for that target. |
| `unique_residences.csv` | Yes | Historical listing catalog for that target. |

There is no execution-history CSV. The only report marker kept in Git is the tiny per-target `daily_report_log.csv`, because it prevents duplicate daily reports.

## Alerts And Reports

Immediate alerts are sent when a target has added or removed listings. Subjects use the target name:

```text
CROUS Bordeaux: +2 / -0 logements
CROUS Strasbourg: +1 / -0 logements
```

Daily reports are sent once per target per CET day when `send_daily_report` is true and the current time is inside that target's `daily_report_time_window`.

Default target report window:

```text
23->00
```

That means reports are eligible from 23:00 up to, but not including, 00:00 CET. The sent marker prevents repeats during that window.

The workflow also uses GitHub Actions `concurrency` and a shallow branch checkout before scraping. If cron-job.org triggers runs every two minutes during the report window, runs queue instead of overlapping, and each queued run checks out the latest committed `daily_report_log.csv` before deciding whether to send.

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
- serializes runs with GitHub Actions concurrency
- uses shallow checkout to avoid fetching the full bloated history on every run
- commits updated target-folder CSVs under `data/`

Required repository secrets:

| Secret | Value |
| --- | --- |
| `BREVO_LOGIN` | Brevo SMTP login. |
| `BREVO_API_KEY` | Brevo SMTP key/password. |
| `FROM_EMAIL` | Verified Brevo sender email. |
| `TO_EMAIL` | Main/test recipient email. |
| `FRIEND_TO_EMAIL` | Strasbourg recipient email when production config is restored. |

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

## Cleanup Rules

Do not commit:

- root-level generated CSVs
- `data/test_jabrane_main/`, the old empty test output folder
- empty or old test CSV files

Keep useful Bordeaux history, including existing `bordeaux_data/`, unless you intentionally migrate it into the newer `data/bordeaux/` layout.

## History Cleanup

This PR stops new bloat, but old generated CSVs still live in Git history until the repository history is rewritten.

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
  --path data/test_jabrane_main \
  --invert-paths
git push --force --mirror
```

Before force-pushing rewritten history, export any old production CSVs you still want outside Git, tell collaborators to reclone, and pause cron-job.org so it does not dispatch during the rewrite.
