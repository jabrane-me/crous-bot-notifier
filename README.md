# CROUS Housing Notifier Bot (Completely Free)

## 🚀 Overview

This project is an automated web scraping bot designed to monitor the French CROUS housing website (`trouverunlogement.lescrous.fr`) for new residence availabilities. It's built to run automatically on a schedule using GitHub Actions, providing a powerful, serverless, and free solution for students and individuals looking for housing.

When changes are detected, the bot sends detailed email notifications, ensuring you're among the first to know about new opportunities. This was specifically developed to aid in the search for student housing in cities like Bordeaux.

## ✨ Features

- **Multi-Target Monitoring:** Scrape multiple CROUS search URLs simultaneously, each with its own configuration.
- **Persistent Data Storage:** Uses CSV files to track available, removed, and daily activity for each target.
- **Smart Change Detection:** Intelligently compares the current state with the last known state to identify what's new.
- **Pagination Support:** Automatically detects and scrapes all pages of results for a given search.
- **Dual Notification System:**
    - **Immediate Alerts:** Sends an email the moment a change is detected.
    - **Daily Summary Report:** Sends a single, comprehensive email at a set time each day, summarizing all of the day's activity.
- **Highly Configurable:** Easily enable or disable immediate alerts and daily summaries for each target URL.
- **Organized Data:** Stores data for each monitoring target in separate, clearly named folders.
- **Price-Sorted Emails:** All lists of residences in emails are sorted by price (lowest to highest) for easy viewing.
- **Serverless & Free:** Runs entirely on the free tiers of GitHub Actions and an email service provider (like Brevo or SendGrid).

## ⚙️ How It Works

The system is composed of a single Python script orchestrated by GitHub Actions.

1.  **Scheduler:** A GitHub Actions workflow (`.github/workflows/run_check.yml`) is configured to run on a schedule (e.g., every hour).
2.  **Scraper:** The Python script (`crous_notifier.py`) is executed. It scrapes the target URLs, handles pagination, and gathers all available housing data.
3.  **Comparator:** The script compares the newly scraped data against the state saved in the CSV files from the previous run.
4.  **Notifier:** If changes are detected (and alerts are enabled), it sends an immediate email.
5.  **Summarizer:** At a specific time (e.g., 22:00 CET), the script generates and sends a daily summary report if enabled.
6.  **Data Logger:** All changes and actions are logged to the respective CSV files, which are then committed and pushed back to the repository by the GitHub Action, ensuring the state is saved for the next run.

## 🛠️ Setup and Installation

Follow these steps to get your own CROUS Notifier Bot running.

### 1. Prerequisites

- A **GitHub Account**.
- A free account with an email service provider. This guide uses **Brevo (formerly Sendinblue)** as it offers a generous free tier (300 emails/day).
    - Sign up at [brevo.com](https://www.brevo.com/).
    - Get your **SMTP credentials**: Server, Port, Login, and SMTP Key (API Key).
    - **Verify a Sender Email:** You must add and verify the email address you will send from.

### 2. Fork or Clone the Repository

Get the code into your own GitHub account. You can either fork this repository or create a new one and upload the files.

### 3. Configure the Python Script

Open `crous_notifier.py` and edit the `TARGETS_TO_MONITOR` list at the bottom of the file. This is where you define what you want to scrape.

```python
# --- Define all monitoring targets here ---
TARGETS_TO_MONITOR = [
    {
        "url": "https://trouverunlogement.lescrous.fr/tools/41/search",
        "folder_name": ".", # Use "." for the main directory
        "send_immediate_alert": False, # Set to True to get instant emails on change
        "send_daily_report": True
    },
    {
        "url": "https://trouverunlogement.lescrous.fr/tools/41/search?bounds=-0.6386987_44.9161806_-0.5336838_44.8107826",
        "folder_name": "data_bordeaux", # A dedicated folder for this search
        "send_immediate_alert": True,
        "send_daily_report": True
    }
]
```

*   `url`: The full CROUS search URL you want to monitor.
*   `folder_name`: The directory where the data files (.csv) for this specific search will be stored. Use `.` for the main project directory.
*   `send_immediate_alert`: True or False. Set to True if you want an email the moment a change happens.
*   `send_daily_report`: True or False. Set to True to receive the end-of-day summary email.

### 4. Set Up GitHub Secrets

This is the most important step for security. Do not put your credentials in the code.

In your GitHub repository, go to Settings > Secrets and variables > Actions.
Click "New repository secret" for each of the following:

*   `BREVO_LOGIN`: Your SMTP Login from Brevo (e.g., 9350d6001@smtp-brevo.com).
*   `BREVO_API_KEY`: Your SMTP Key (password) from Brevo.
*   `TO_EMAIL`: The email address where you want to receive notifications.
*   `FROM_EMAIL`: Your verified sender email address from Brevo.

*(Note: If you are using the SendGrid version of the script, you would create a secret named `SENDGRID_API_KEY` instead).*

### 5. Configure the Schedule (Optional)

The bot is pre-configured to run every hour. If you want to change this, edit the cron schedule in `.github/workflows/run_check.yml`:

```yaml
schedule:
  # Runs every hour at minute 0
  - cron: '0 * * * *'

  # Example: Runs every 15 minutes
  # - cron: '*/15 * * * *'
```


## ▶️ Usage

Once set up, the bot runs automatically.

*   **Automatic Runs**: The script will execute based on the cron schedule you set.
*   **Manual Runs**: To test the bot immediately, go to the Actions tab in your repository, click on the workflow name ("Check for Crous Housing Hourly"), and use the "Run workflow" button.
*   **Viewing Logs**: You can see the output of every run by clicking on it in the Actions tab. This is useful for debugging.

## 📁 File Structure

```
├── .github/
│   └── workflows/
│       └── run_check.yml       # GitHub Actions workflow for scheduling
├── .gitignore                  # Ignores cache and local files
├── crous_notifier.py           # The main Python script
└── requirements.txt            # List of required Python libraries
```
