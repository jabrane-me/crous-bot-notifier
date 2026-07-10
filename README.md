# CROUS Housing Notifier Bot

A free-tier CROUS housing notifier that runs on GitHub Actions, sends email through Brevo SMTP, and can be triggered reliably by cron-job.org. It monitors one or more CROUS search URLs, stores CSV state per target, sends immediate alerts when listings change, and sends at most one daily report per target.

## Free-Tier Architecture

This project is designed to run without a paid server:

| Service | Purpose |
| --- | --- |
| GitHub repository | Stores the bot code, target config, workflow, and per-target CSV state. |
| GitHub Actions | Runs the scraper and commits updated CSV state. |
| cron-job.org | Triggers the workflow on a regular schedule with `workflow_dispatch`. |
| Brevo SMTP | Sends immediate alert emails and daily report emails. |

## Configure Targets

Targets are configured in `crous_targets.json`. Each target has its own label, recipient secret, data folder, CROUS URL list, alert/report flags, and daily report window.

```json
[
  {
    "name": "Target label",
    "email_env": "TO_EMAIL",
    "data_dir": "data/target_label",
    "cities": ["City 1", "City 2"],
    "urls": [
      "https://trouverunlogement.lescrous.fr/tools/45/search?bounds=..."
    ],
    "send_immediate_alert": true,
    "immediate_alert_filter": {
      "price_min_eur": 260,
      "price_max_eur": 300,
      "surface_min_m2": 13,
      "surface_max_m2": 16
    },
    "send_daily_report": true,
    "daily_report_time_window": {
      "start": "23:30",
      "end": "00:00"
    }
  }
]
```

Use exact CROUS search URLs copied from the website. The bot supports multiple targets and multiple URLs per target.

## Daily Reports

Daily reports are evaluated per target. A report is sent only when:

- `send_daily_report` is `true`
- the current CET time is inside that target's `daily_report_time_window`
- that target's `daily_report_log.csv` does not already contain today's `date_cet`

`daily_report_time_window` uses `HH:MM` values:

```json
{
  "start": "23:30",
  "end": "00:00"
}
```

The example window means reports are eligible from 23:30 up to, but not including, 00:00 CET. Windows can differ per target.
The daily report is an end-of-day summary: available listings first, then listings added today, then listings removed today.

## Immediate Alerts

Immediate alerts are sent when listings are added or removed for a target and `send_immediate_alert` is `true`.

`immediate_alert_filter` is optional. When present, an added or removed listing is included in an immediate email only when its complete price and surface ranges fit inside the configured inclusive bounds. A listing with one price or surface value uses that value as both ends of its range. Each bound is optional, and price and surface conditions are combined with AND when both are configured.

The filter affects immediate emails only. The bot still keeps complete CSV state for every listing, and daily reports remain complete. Targets without `immediate_alert_filter` keep the original unfiltered behavior.

Subject format:

```text
CROUS Target label: +2 / -0 logements
```

Email bodies include price, listing details, address, and link. Internal residence IDs are not shown to recipients.

## CSV State

CSV files inside each target's `data_dir` are bot state and should be committed:

| File | Purpose |
| --- | --- |
| `current_available.csv` | Latest snapshot used to detect additions and removals. |
| `availability_changes.csv` | Add/remove event record for the target. |
| `unique_residences.csv` | Catalog of unique listings seen for the target. |
| `daily_report_log.csv` | Minimal report marker: `date_cet,time_cet,target_name,status,current_count`. |

Generated root-level CSV files from old versions should not be committed.
When listings are unchanged between runs, the bot preserves existing timestamps and does not append new state rows, so GitHub Actions has nothing new to commit.

Generated listing CSVs use this column order:

```text
residence_id, name, housing_type, price_text, price_min_eur, price_max_eur, surface_text, surface_min_m2, surface_max_m2, details, address, link, source_url, first_seen_cet, last_seen_cet
```

If a price or surface has only one number, the value is written to the `min` column and the matching `max` column stays empty.

## GitHub Secrets

Create these repository secrets in **Settings -> Secrets and variables -> Actions**:

| Secret | Value |
| --- | --- |
| `BREVO_LOGIN` | Brevo SMTP login. |
| `BREVO_API_KEY` | Brevo SMTP key/password. |
| `FROM_EMAIL` | Verified Brevo sender email. |
| `TO_EMAIL` | Recipient email for any target using `email_env: "TO_EMAIL"`. |

Add one secret for each additional `email_env` value used in `crous_targets.json`.

## Brevo Setup

1. Create a Brevo account.
2. Verify the sender email or domain.
3. Open the Brevo SMTP settings.
4. Store the SMTP login as `BREVO_LOGIN`.
5. Store the SMTP key/password as `BREVO_API_KEY`.
6. Store the verified sender as `FROM_EMAIL`.

Never commit real email addresses, SMTP usernames, or SMTP keys.

## GitHub Actions

The workflow is `.github/workflows/run_check.yml`.

It:

- supports `workflow_dispatch` for cron-job.org
- keeps a small scheduled run so GitHub does not disable the workflow
- uses shallow checkout to keep workflow startup fast
- installs `requirements.txt`
- runs `python crous_notifier.py`
- commits updated target-folder CSVs under `data/`
- uses GitHub Actions `concurrency` so frequent triggers queue instead of overlapping

## cron-job.org Setup

Create a cron-job.org job that calls GitHub's workflow dispatch API.

Request:

```text
POST https://api.github.com/repos/OWNER/REPO/actions/workflows/run_check.yml/dispatches
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
- `requirements.txt`
- `crous_targets.json`
- `.github/workflows/run_check.yml`
- useful per-target CSV state under `data/`
- any intentionally retained legacy target data folder

Do not commit:

- root-level generated CSVs
- obsolete test output folders
- temporary local files
- cache folders
- credentials

## Local Run

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Run the bot:

```bash
python crous_notifier.py
```

Local runs may create or update CSV files in target data folders.
