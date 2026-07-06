# CROUS Housing Notifier Bot

A CROUS availability notifier for students who need fast alerts while units appear and disappear quickly. It scrapes configured `trouverunlogement.lescrous.fr` searches, stores clean CSV state/history, and sends each person alerts only for their own target searches.

## What changed

- **Config is now outside Python:** edit `crous_targets.json` instead of touching `crous_notifier.py`.
- **Two-email setup:** each target can point to a different email secret through `email_env`.
- **Clean CSVs:** the bot writes current availability, timestamped add/remove logs, unique historical residences, and run logs.
- **Useful parsing, not overkill:** price and surface min/max are parsed; the full address is kept as-is without trying to split city/postal code.
- **Cron-job.org friendly:** GitHub Actions keeps `workflow_dispatch` enabled so an external cron can trigger checks more reliably than GitHub's delayed schedules.

## Target configuration file

Edit [`crous_targets.json`](crous_targets.json). This file is safe to commit because it references secret names, not private emails or passwords.

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

- `name`: label used in logs/email subjects.
- `email_env`: environment variable or GitHub secret name that contains the recipient email.
- `data_dir`: folder for this target's CSVs.
- `cities`: notes for humans; the script does not parse addresses into city fields.
- `urls`: one or more CROUS search URLs copied from the CROUS website after setting filters/map bounds.
- `send_immediate_alert`: send an email when additions/removals are detected.
- `send_daily_report`: kept for future compatibility; current workflow focuses on immediate alerts and CSV history.

If you want to use another config filename locally, set `TARGETS_CONFIG_PATH=/path/to/file.json`.

## CSV outputs

For every configured `data_dir`, the bot writes:

- `current_available.csv`: only residences currently visible in the latest scrape.
- `availability_changes.csv`: append-only log of `added` / `removed` events with `timestamp_cet`.
- `unique_residences.csv`: every residence/type variant ever seen, with `seen_count`, `last_event`, and `removed_at_cet`.
- `run_log.csv`: scrape count, current count, added/removed count, and partial/error status.

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

## Re-setting up Brevo

1. Create/login to a Brevo account.
2. Verify the sender email you want to send from.
3. Go to **SMTP & API** in Brevo.
4. Copy the SMTP login into `BREVO_LOGIN`.
5. Create/copy an SMTP key into `BREVO_API_KEY`.
6. Use the verified sender email as `FROM_EMAIL`.

The bot uses Brevo's SMTP relay at `smtp-relay.brevo.com:587`.

## GitHub Actions secrets and variables

Go to your repository: **Settings → Secrets and variables → Actions → New repository secret**.

Create these **secrets**:

- `BREVO_LOGIN`: Brevo SMTP login.
- `BREVO_API_KEY`: Brevo SMTP key/password.
- `FROM_EMAIL`: verified Brevo sender email.
- `TO_EMAIL`: your recipient email.
- `FRIEND_TO_EMAIL`: your friend's recipient email.

You usually do **not** need a GitHub Actions variable for the target config because `crous_targets.json` is committed. Only use a variable/secret for config if it contains private URLs or emails, which is not recommended here.

## GitHub workflow and cron-job.org setup

The workflow lives in `.github/workflows/run_check.yml` and supports:

- `workflow_dispatch`: manual/API trigger, best for cron-job.org.
- A daily GitHub `schedule`: only to keep the workflow alive.

### Personal access token for cron-job.org

cron-job.org needs to call the GitHub Actions workflow dispatch API. For that you need a GitHub token.

Recommended modern option:

1. GitHub → **Settings → Developer settings → Personal access tokens → Fine-grained tokens**.
2. Generate a token scoped only to this repository.
3. Give it **Actions: Read and write** permission. If GitHub also asks for repository contents metadata, allow the minimum required metadata/read access.
4. Set an expiration you are comfortable with.
5. Copy the token once and store it in cron-job.org, not in the repo.

Classic token fallback:

- Create a classic PAT with the `repo` scope for a private repo, or `public_repo` for a public repo. Fine-grained is safer if available.

### cron-job.org request

Create a cron job that runs every 5 minutes or whatever interval you want.

- Method: `POST`
- URL:

```text
https://api.github.com/repos/OWNER/REPO/actions/workflows/run_check.yml/dispatches
```

Replace `OWNER` and `REPO` with your GitHub owner/repository.

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_PAT
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
```

Body:

```json
{"ref":"main"}
```

If your branch is not `main`, replace it with the branch where the workflow exists.

## Running locally

```bash
python -m pip install -r requirements.txt
TO_EMAIL=you@example.com FRIEND_TO_EMAIL=friend@example.com python crous_notifier.py
```

Email sending is skipped unless Brevo credentials, `FROM_EMAIL`, and the target recipient env var are present.
