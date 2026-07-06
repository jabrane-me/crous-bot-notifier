# CROUS Housing Notifier Bot

A CROUS availability notifier for students who need fast alerts while listings appear and disappear quickly. It scrapes configured `trouverunlogement.lescrous.fr` searches, stores CSV state/history, and sends per-target email alerts.

## What changed

- **Config is now outside Python:** edit `crous_targets.json` instead of touching `crous_notifier.py`.
- **Multi-person setup:** each target points to its own email secret via `email_env`.
- **Clean CSV state/history:** current availability, add/remove logs, unique historical residences, and run logs.
- **Useful parsing:** price/surface min-max are parsed while preserving raw address text.
- **cron-job.org friendly:** `workflow_dispatch` stays enabled so external scheduling is reliable.

## How the bot works

1. `crous_targets.json` defines each monitored target/search.
2. GitHub Actions injects private values (recipient emails and Brevo credentials) through secrets.
3. cron-job.org triggers GitHub's workflow dispatch API.
4. The workflow runs `python crous_notifier.py`.
5. The script scrapes CROUS, compares with previous CSV state, sends alerts on changes, writes CSV history, and the workflow commits updated CSV files.

## Files and secrets

| Item | Purpose |
| --- | --- |
| `crous_targets.json` | Non-secret target config: names, city notes, data folders, CROUS URLs, and `email_env` names. |
| `.github/workflows/run_check.yml` | Workflow that runs the scraper and commits CSV updates. |
| GitHub Actions Secrets | Private values: Brevo credentials and actual recipient emails. |

You should not need to edit `crous_notifier.py` for normal setup.

## Configure targets

Edit [`crous_targets.json`](crous_targets.json). This file is safe to commit because it references secret names, not private emails/passwords.

```json
[
  {
    "name": "Bordeaux",
    "email_env": "TO_EMAIL",
    "data_dir": "data/bordeaux",
    "cities": ["Bordeaux", "Pessac", "Talence", "Mérignac"],
    "urls": [
      "https://trouverunlogement.lescrous.fr/tools/41/search?bounds=-0.6386987_44.9161806_-0.5336838_44.8107826"
    ],
    "send_immediate_alert": true,
    "send_daily_report": false
  },
  {
    "name": "Friend target cities",
    "email_env": "FRIEND_TO_EMAIL",
    "data_dir": "data/friend",
    "cities": ["Replace with friend's target cities"],
    "urls": [
      "https://trouverunlogement.lescrous.fr/tools/41/search"
    ],
    "send_immediate_alert": true,
    "send_daily_report": false
  }
]
```

| Field | Meaning |
| --- | --- |
| `name` | Label used in emails and logs. |
| `email_env` | Environment variable/GitHub secret name containing the recipient email. |
| `data_dir` | Folder where this target's CSV files are stored. |
| `cities` | Human notes only; address text is kept as-is. |
| `urls` | One or more CROUS search URLs copied from the CROUS website. |
| `send_immediate_alert` | Sends email when additions/removals are detected. |
| `send_daily_report` | Kept for compatibility; current flow focuses on immediate alerts and CSV history. |

If you want another config file locally, set `TARGETS_CONFIG_PATH=/path/to/file.json`.

### Add another person

Add another object to `crous_targets.json`, for example:

```json
{
  "name": "Lyon for Sara",
  "email_env": "SARA_EMAIL",
  "data_dir": "data/sara_lyon",
  "cities": ["Lyon", "Villeurbanne"],
  "urls": [
    "PASTE_CROUS_SEARCH_URL_HERE"
  ],
  "send_immediate_alert": true,
  "send_daily_report": false
}
```

Then create the matching GitHub secret (`SARA_EMAIL`) and expose it in workflow `env:`.

### One person with multiple searches

Use multiple URLs in one target:

```json
"urls": [
  "CROUS_URL_FOR_AREA_1",
  "CROUS_URL_FOR_AREA_2",
  "CROUS_URL_FOR_AREA_3"
]
```

The script deduplicates listings across URLs and sends one alert per target.

## Get CROUS search URLs

1. Open `https://trouverunlogement.lescrous.fr/`.
2. Apply filters/map bounds.
3. Copy the final browser URL.
4. Paste it into the target's `urls` array.

Use tight map bounds/filters for competitive cities so alerts stay actionable.

## Set up Brevo SMTP

The bot sends through Brevo SMTP: `smtp-relay.brevo.com:587`.

1. Create or log in to Brevo.
2. Verify sender email/domain.
3. Go to **SMTP & API**.
4. Put SMTP login in `BREVO_LOGIN`.
5. Put SMTP key/password in `BREVO_API_KEY`.
6. Put verified sender email in `FROM_EMAIL`.

Never commit Brevo credentials.

## GitHub Actions secrets

In your repository, go to **Settings → Secrets and variables → Actions** and create:

| Secret | Value |
| --- | --- |
| `BREVO_LOGIN` | Brevo SMTP login. |
| `BREVO_API_KEY` | Brevo SMTP key/password. |
| `FROM_EMAIL` | Verified sender email. |
| `TO_EMAIL` | Your recipient email. |
| `FRIEND_TO_EMAIL` | Your friend's recipient email. |

For every new `email_env`, add a matching secret.

## Workflow and cron-job.org setup

The workflow is in `.github/workflows/run_check.yml` and supports:

- `workflow_dispatch` (manual/API trigger, recommended for cron-job.org)
- daily `schedule` (mainly to keep workflow active)

### Pass extra recipient secrets to the workflow

If you add more people, include their secret in workflow `env:`:

```yaml
env:
  BREVO_LOGIN: ${{ secrets.BREVO_LOGIN }}
  BREVO_API_KEY: ${{ secrets.BREVO_API_KEY }}
  TO_EMAIL: ${{ secrets.TO_EMAIL }}
  FRIEND_TO_EMAIL: ${{ secrets.FRIEND_TO_EMAIL }}
  SARA_EMAIL: ${{ secrets.SARA_EMAIL }}
  FROM_EMAIL: ${{ secrets.FROM_EMAIL }}
```

If a target's `email_env` is missing from workflow `env:`, that target is skipped.

### Create a GitHub PAT for cron-job.org

Recommended: fine-grained token scoped to this repository with **Actions: Read and write**.

Classic fallback:

- private repo: `repo`
- public repo: `public_repo`

Store the token in cron-job.org only.

### cron-job.org request

- Method: `POST`
- URL:

```text
https://api.github.com/repos/jabrane-me/crous-bot-notifier/actions/workflows/run_check.yml/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: ******
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Body:

```json
{"ref":"main"}
```

Use another branch name if needed.

### Verify cron-job.org worked

1. Open GitHub **Actions** for the repository.
2. Open latest workflow run.
3. Check scraper logs.
4. Check committed CSV updates.
5. If changes were detected, check recipient inbox.

## CSV outputs

For every configured `data_dir`, the bot writes:

| File | Purpose |
| --- | --- |
| `current_available.csv` | Latest visible listings only. |
| `availability_changes.csv` | Append-only add/remove event log with timestamps. |
| `unique_residences.csv` | Historical catalog of unique residence/unit variants. |
| `run_log.csv` | Scrape/change counts and partial failure info. |

Main columns include:

- `residence_id`
- `name`
- `housing_type`
- `price_text`, `price_min_eur`, `price_max_eur`
- `surface_text`, `surface_min_m2`, `surface_max_m2`
- `address`
- `details`
- `link`
- `source_url`
- `first_seen_cet`, `last_seen_cet`

## Run locally

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Dry run:

```bash
python crous_notifier.py
```

Run with recipient env vars:

```bash
TO_EMAIL=you@example.com FRIEND_TO_EMAIL=friend@example.com python crous_notifier.py
```

Send emails locally:

```bash
BREVO_LOGIN=... BREVO_API_KEY=... FROM_EMAIL=verified@example.com TO_EMAIL=you@example.com python crous_notifier.py
```

## Troubleshooting

### Target is skipped

Check that:

- matching secret exists
- workflow passes it under `env:`
- `email_env` exactly matches that secret name

### cron-job.org returns 401 or 403

Check that:

- PAT is correct and not expired
- `Authorization: ******` header is present
- token has Actions read/write permission

### cron-job.org returns 404

Check that:

- owner/repo is correct
- workflow filename is `run_check.yml`
- branch in body contains that workflow file

### No email arrives

Check that:

- Brevo sender is verified
- `BREVO_LOGIN`, `BREVO_API_KEY`, and `FROM_EMAIL` are correct
- recipient secret exists and is passed to workflow
- there were actual additions/removals (alerts only send on changes)

### CSVs changed but were not committed

Workflow needs:

```yaml
permissions:
  contents: write
```

The included workflow already has this.
