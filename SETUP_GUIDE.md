# CROUS Bot Setup Guide

This guide matches the current config-driven version of the CROUS notifier. The short version: edit `crous_targets.json`, put private values in GitHub Actions Secrets, and use cron-job.org to trigger the GitHub workflow frequently.

## How the bot works

1. `crous_targets.json` lists each person/search target.
2. GitHub Actions provides private values such as recipient emails and Brevo SMTP credentials through Secrets.
3. cron-job.org triggers the workflow using GitHub's `workflow_dispatch` API.
4. The workflow runs `python crous_notifier.py`.
5. The script scrapes CROUS, compares results with the last CSV snapshot, sends emails on changes, writes CSV history, and the workflow commits CSV updates back to the repo.

## Files and secrets

| Item | Purpose |
| --- | --- |
| `crous_targets.json` | Non-secret target config: names, city notes, data folders, CROUS URLs, and `email_env` names. |
| `.github/workflows/run_check.yml` | Workflow that runs the scraper and commits CSV updates. |
| GitHub Actions Secrets | Private values: Brevo credentials and actual recipient emails. |

You should not need to edit `crous_notifier.py` for normal setup.

## Configure targets and multiple people

Edit `crous_targets.json`.

Example:

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

Fields:

| Field | Meaning |
| --- | --- |
| `name` | Label used in emails and logs. |
| `email_env` | Environment variable / GitHub secret name containing the recipient email. |
| `data_dir` | Folder where this target's CSV files are stored. Use a different folder per person/search group. |
| `cities` | Human notes only. The script keeps CROUS address text as-is. |
| `urls` | One or more CROUS search URLs copied from the CROUS website. |
| `send_immediate_alert` | Send an email when added/removed listings are detected. |
| `send_daily_report` | Reserved for compatibility; the current flow focuses on immediate alerts and CSV history. |

### Add another person

Add another object to `crous_targets.json`:

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

Then create a GitHub Actions secret named `SARA_EMAIL` and pass it in the workflow environment.

### One person with multiple searches

Put several CROUS URLs in one target:

```json
"urls": [
  "CROUS_URL_FOR_AREA_1",
  "CROUS_URL_FOR_AREA_2",
  "CROUS_URL_FOR_AREA_3"
]
```

The script deduplicates listings across those URLs and sends one email for that target.

## Get CROUS search URLs

1. Open `https://trouverunlogement.lescrous.fr/`.
2. Search/filter the city or map area you care about.
3. Copy the final browser URL.
4. Paste it into the target's `urls` array.

Use tight map bounds/filters for competitive cities so emails stay actionable.

## Set up Brevo SMTP

The bot sends through Brevo SMTP: `smtp-relay.brevo.com:587`.

1. Create or log in to Brevo: `https://www.brevo.com/`.
2. Verify the sender email or domain.
3. Go to **SMTP & API**.
4. Copy the SMTP login into GitHub secret `BREVO_LOGIN`.
5. Create/copy an SMTP key into GitHub secret `BREVO_API_KEY`.
6. Put the verified sender email in GitHub secret `FROM_EMAIL`.

Never commit Brevo credentials.

## GitHub Actions secrets

In your GitHub repo:

1. Go to **Settings** → **Secrets and variables** → **Actions**.
2. Click **New repository secret**.
3. Add:

| Secret | Value |
| --- | --- |
| `BREVO_LOGIN` | Brevo SMTP login. |
| `BREVO_API_KEY` | Brevo SMTP key/password. |
| `FROM_EMAIL` | Verified sender email. |
| `TO_EMAIL` | Your recipient email. |
| `FRIEND_TO_EMAIL` | Your friend's recipient email. |

For every new `email_env`, add a matching secret. Example: `"email_env": "SARA_EMAIL"` requires a `SARA_EMAIL` secret.

## Pass extra people through the workflow

The workflow already passes `TO_EMAIL` and `FRIEND_TO_EMAIL`. If you add more people, update `.github/workflows/run_check.yml`:

```yaml
env:
  BREVO_LOGIN: ${{ secrets.BREVO_LOGIN }}
  BREVO_API_KEY: ${{ secrets.BREVO_API_KEY }}
  TO_EMAIL: ${{ secrets.TO_EMAIL }}
  FRIEND_TO_EMAIL: ${{ secrets.FRIEND_TO_EMAIL }}
  SARA_EMAIL: ${{ secrets.SARA_EMAIL }}
  FROM_EMAIL: ${{ secrets.FROM_EMAIL }}
```

If a target's `email_env` is not available in the workflow environment, that target is skipped.

## Use cron-job.org

GitHub scheduled workflows can be delayed. For fast housing checks, use cron-job.org to trigger the workflow every few minutes.

### Create a GitHub PAT

Recommended: fine-grained token.

1. GitHub → profile picture → **Settings**.
2. **Developer settings** → **Personal access tokens** → **Fine-grained tokens**.
3. Generate a token for only this repository.
4. Grant **Actions: Read and write**.
5. Keep required metadata/read permission.
6. Set an expiration and copy the token once.

Classic fallback:

- Public repo: classic PAT with `public_repo`.
- Private repo: classic PAT with `repo`.

Store the PAT in cron-job.org only. Do not commit it.

### cron-job.org request

Create a cron job:

- Schedule: every 5 minutes, or your preferred interval.
- Method: `POST`.
- URL:

```text
https://api.github.com/repos/jabrane-me/crous-bot-notifier/actions/workflows/run_check.yml/dispatches
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_PAT
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Body for `main`:

```json
{"ref":"main"}
```

If testing another branch, use that branch name:

```json
{"ref":"work"}
```

### Verify cron-job.org worked

1. Go to GitHub → repo → **Actions**.
2. Open the latest workflow run.
3. Check scraper logs.
4. Check committed CSV updates.
5. If there were changes, check the recipient inbox.

## CSV outputs

Each target writes CSV files under its configured `data_dir`.

| File | Purpose |
| --- | --- |
| `current_available.csv` | Latest visible listings only. |
| `availability_changes.csv` | Append-only add/remove event log with timestamps. |
| `unique_residences.csv` | Historical catalog of unique residence/unit variants. |
| `run_log.csv` | Operational scrape/change counts and partial failures. |

Main columns:

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

Actually send email locally:

```bash
BREVO_LOGIN=... BREVO_API_KEY=... FROM_EMAIL=verified@example.com TO_EMAIL=you@example.com python crous_notifier.py
```

Use a different config file:

```bash
TARGETS_CONFIG_PATH=./my_targets.json python crous_notifier.py
```

## Troubleshooting

### Target is skipped

Check that:

- The matching GitHub secret exists.
- The workflow passes it under `env:`.
- `email_env` matches the secret name exactly.

### cron-job.org returns 401 or 403

Check that:

- The PAT is correct and not expired.
- Header is `Authorization: Bearer YOUR_GITHUB_PAT`.
- The token has Actions read/write permission.

### cron-job.org returns 404

Check that:

- Owner/repo is correct.
- Workflow filename is `run_check.yml`.
- The branch in the JSON body contains that workflow file.

### No email arrives

Check that:

- Brevo sender is verified.
- `BREVO_LOGIN`, `BREVO_API_KEY`, and `FROM_EMAIL` are correct.
- Recipient secret exists and is passed to the workflow.
- There were actual additions/removals; immediate alerts only send on changes.

### CSVs changed but were not committed

The workflow needs:

```yaml
permissions:
  contents: write
```

The included workflow already has this.
