# CROUS Housing Notifier Bot

A GitHub Actions + cron-job.org bot that watches CROUS housing searches and emails the right person when units appear or disappear. It is designed for the real student-housing rush: checks can be triggered every few minutes by cron-job.org, CSV state is committed back to the repo, and each person can have their own cities/search URLs.

## Quick mental model

1. You edit `crous_targets.json` with the CROUS search URLs you care about.
2. Recipient emails and Brevo credentials live in GitHub Actions **Secrets**.
3. cron-job.org calls the GitHub Actions `workflow_dispatch` API every few minutes.
4. The workflow runs `python crous_notifier.py`.
5. The script scrapes CROUS, compares with the previous CSV snapshot, emails changes, writes CSV logs, and GitHub Actions commits the CSV updates back to the repo.

## Files you normally edit

| File | Purpose |
| --- | --- |
| `crous_targets.json` | Search targets, city labels, data folders, and which secret/env var contains each recipient email. |
| `.github/workflows/run_check.yml` | GitHub Actions workflow. Usually you only keep it as-is. |
| GitHub Actions Secrets | Private values: Brevo SMTP credentials and recipient emails. |

You should **not** need to edit `crous_notifier.py` for normal usage.

## 1. Configure targets and multiple people

Open [`crous_targets.json`](crous_targets.json). The file is committed on purpose because it contains non-secret configuration. It references email secret names through `email_env` instead of hardcoding actual emails.

Current structure:

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

### What each field means

| Field | Required? | Meaning |
| --- | --- | --- |
| `name` | Yes | Human label used in email subjects and logs. |
| `email_env` | Yes | Name of the environment variable / GitHub secret containing the recipient email. Example: `TO_EMAIL`. |
| `data_dir` | Yes | Folder where this target's CSV files are stored. Use a different folder per person/search group. |
| `cities` | No | Human notes only. The script does not split addresses into city/postal-code columns. |
| `urls` | Yes | One or more CROUS search URLs copied from the CROUS website after applying filters/map bounds. |
| `send_immediate_alert` | No | `true` means send an email when added/removed listings are detected. |
| `send_daily_report` | No | Reserved for future compatibility. Current flow focuses on immediate alerts + CSV history. |

### Add more people

Add another object to `crous_targets.json`, give it a new `email_env`, then create a matching GitHub Actions secret.

Example for a third person:

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

Then add a GitHub Actions secret named `SARA_EMAIL` with Sara's email address.

### One person, multiple cities

Put multiple CROUS URLs in the same `urls` list. The script deduplicates results across those URLs and sends one email for that target.

```json
"urls": [
  "CROUS_URL_FOR_CITY_OR_AREA_1",
  "CROUS_URL_FOR_CITY_OR_AREA_2",
  "CROUS_URL_FOR_CITY_OR_AREA_3"
]
```

### Multiple people with the same city

Create separate target objects with separate `data_dir` values. This lets each person have separate CSV history and email behavior.

## 2. Get CROUS search URLs

1. Go to `https://trouverunlogement.lescrous.fr/`.
2. Search/filter the city or map area you care about.
3. Copy the final URL from the browser address bar.
4. Paste it into the target's `urls` array in `crous_targets.json`.

Tip: for fast-moving cities, use tighter map bounds/filters so emails stay actionable.

## 3. Set up Brevo SMTP

Brevo is used only to send emails. The script sends through `smtp-relay.brevo.com:587`.

1. Create or log in to a Brevo account: `https://www.brevo.com/`.
2. Verify the sender email/domain you want the bot to send from.
3. In Brevo, go to **SMTP & API**.
4. Copy the **SMTP login**. This becomes `BREVO_LOGIN`.
5. Create or copy an **SMTP key**. This becomes `BREVO_API_KEY`.
6. Choose the verified sender email. This becomes `FROM_EMAIL`.

Do not put Brevo credentials in `crous_targets.json` or in Python.

## 4. Add GitHub Actions secrets

In GitHub:

1. Open your repo.
2. Go to **Settings** → **Secrets and variables** → **Actions**.
3. Click **New repository secret**.
4. Add these secrets:

| Secret name | Value |
| --- | --- |
| `BREVO_LOGIN` | Brevo SMTP login. |
| `BREVO_API_KEY` | Brevo SMTP key/password. |
| `FROM_EMAIL` | Verified Brevo sender email. |
| `TO_EMAIL` | Your recipient email. |
| `FRIEND_TO_EMAIL` | Your friend's recipient email. |

For every extra `email_env` you add in `crous_targets.json`, add a matching secret. For example, if a target uses `"email_env": "SARA_EMAIL"`, create a `SARA_EMAIL` secret.

## 5. Make GitHub Actions pass extra people to the script

The workflow must expose each recipient secret as an environment variable. The default workflow already passes `TO_EMAIL` and `FRIEND_TO_EMAIL`.

If you add `SARA_EMAIL`, update `.github/workflows/run_check.yml` under the `Run the housing check script` step:

```yaml
env:
  BREVO_LOGIN: ${{ secrets.BREVO_LOGIN }}
  BREVO_API_KEY: ${{ secrets.BREVO_API_KEY }}
  TO_EMAIL: ${{ secrets.TO_EMAIL }}
  FRIEND_TO_EMAIL: ${{ secrets.FRIEND_TO_EMAIL }}
  SARA_EMAIL: ${{ secrets.SARA_EMAIL }}
  FROM_EMAIL: ${{ secrets.FROM_EMAIL }}
```

If the environment variable is not passed here, the script will skip that target because it has no recipient email.

## 6. Use cron-job.org for fast checks

GitHub scheduled workflows can be delayed, especially at busy times. For housing alerts, use cron-job.org to trigger the workflow every few minutes through GitHub's workflow dispatch API.

### What you need

You need a GitHub personal access token (PAT) that can trigger Actions workflow dispatches.

### Create a fine-grained PAT, recommended

1. GitHub → profile picture → **Settings**.
2. Go to **Developer settings** → **Personal access tokens** → **Fine-grained tokens**.
3. Click **Generate new token**.
4. Choose your account as resource owner.
5. Select only this repository.
6. Set an expiration date.
7. Under repository permissions, grant **Actions: Read and write**.
8. Keep metadata/read permission if GitHub requires it.
9. Generate and copy the token once.

Store this token in cron-job.org only. Do not commit it and do not put it in `crous_targets.json`.

### Classic PAT fallback

If fine-grained tokens do not work for your account/repo:

- Public repo: create a classic PAT with `public_repo`.
- Private repo: create a classic PAT with `repo`.

Fine-grained tokens are safer because they can be scoped to one repo.

### Create the cron-job.org job

In cron-job.org:

1. Create a new cron job.
2. Set the schedule. For competitive housing, every 5 minutes is reasonable.
3. Use method `POST`.
4. URL:

```text
https://api.github.com/repos/jabrane-me/crous-bot-notifier/actions/workflows/run_check.yml/dispatches
```

5. Add headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_PAT
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

6. Add body:

```json
{"ref":"main"}
```

Use the branch where the workflow file exists. If you are testing a branch named `work`, use:

```json
{"ref":"work"}
```

### How to know cron-job.org worked

After cron-job.org runs:

1. Go to GitHub → your repo → **Actions**.
2. Open the workflow run.
3. Check the logs for scraping output.
4. Check whether CSV files changed in the repo.
5. If there were new/removed residences, check the recipient inbox.

## 7. What CSV files are saved

Each target writes files under its configured `data_dir`.

| File | Purpose |
| --- | --- |
| `current_available.csv` | Latest available listings only. |
| `availability_changes.csv` | Append-only add/remove event log with timestamps. |
| `unique_residences.csv` | Historical catalog of every unique residence/unit variant seen. |
| `run_log.csv` | Operational log with scrape counts, change counts, and partial failures. |

Main CSV columns:

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

`residence_id` includes stable listing data such as link, name, address, housing type, price text, and surface text so same-name residences with different unit types are not collapsed into one row.

## 8. Run locally

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Dry run without sending emails:

```bash
python crous_notifier.py
```

Run with local recipient env vars:

```bash
TO_EMAIL=you@example.com FRIEND_TO_EMAIL=friend@example.com python crous_notifier.py
```

To actually send email locally, also provide Brevo credentials:

```bash
BREVO_LOGIN=... BREVO_API_KEY=... FROM_EMAIL=verified@example.com TO_EMAIL=you@example.com python crous_notifier.py
```

Use a different config file locally:

```bash
TARGETS_CONFIG_PATH=./my_targets.json python crous_notifier.py
```

## Troubleshooting

### The script says it is skipping a target

That means the target's `email_env` did not resolve to an environment variable. Check:

- The secret exists in GitHub Actions.
- The workflow passes it under `env:`.
- The `email_env` value in `crous_targets.json` matches exactly.

### cron-job.org returns 401 or 403

Check:

- The PAT is copied correctly.
- Header is `Authorization: Bearer YOUR_GITHUB_PAT`.
- The token has Actions read/write permission for this repo.
- The token is not expired.

### cron-job.org returns 404

Check:

- Owner/repo in the URL is correct.
- Workflow filename is `run_check.yml`.
- The branch in the JSON body contains that workflow file.

### No email arrives

Check:

- Brevo sender is verified.
- `BREVO_LOGIN`, `BREVO_API_KEY`, and `FROM_EMAIL` are correct secrets.
- Recipient secret exists and is passed to the workflow.
- There were actual additions/removals; the bot only sends immediate alerts on changes.

### CSVs changed but GitHub did not commit them

Check the workflow permissions. `.github/workflows/run_check.yml` needs:

```yaml
permissions:
  contents: write
```

The included workflow already has this.
