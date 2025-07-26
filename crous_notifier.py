# Final Crous Notifier Script
# Includes both immediate change notifications AND a comprehensive daily summary report.

import requests
from bs4 import BeautifulSoup
import csv
import os
from datetime import datetime, timezone, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import pprint

# --- Configuration ---
URL = "https://trouverunlogement.lescrous.fr/tools/41/search"
AVAILABLE_CSV = 'available_residences.csv'
REMOVED_LOG_CSV = 'removed_residences.log.csv'
DAILY_ACTIVITY_LOG_CSV = 'daily_activity_log.csv'
REPORT_LOG_CSV = 'daily_report_log.csv'

# --- PRODUCTION: Email configuration is now read from environment variables ---
# These will be set in your GitHub repository's "Secrets" settings.
SENDGRID_API_KEY = os.environ.get("SENDGRID_API_KEY")
TO_EMAIL = os.environ.get("TO_EMAIL")
FROM_EMAIL = os.environ.get("FROM_EMAIL")
SENDER_NAME = "CROUS BOT Notifier"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
CET = timezone(timedelta(hours=1))

# --- Core Scraping and File Functions ---

def scrape_crous_page(url):
    """Scrapes the Crous page and returns a list of dictionaries for each residence."""
    try:
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')
        cards = soup.find_all('div', class_='fr-card')
        data = []
        for card in cards:
            residence = {}
            title_element = card.find('h3', class_='fr-card__title')
            if title_element and title_element.find('a'):
                residence['name'] = title_element.find('a').get_text(strip=True)
                residence['link'] = "https://trouverunlogement.lescrous.fr" + title_element.find('a')['href']
                residence['price'] = card.find('p', class_='fr-badge').get_text(strip=True) if card.find('p', class_='fr-badge') else 'N/A'
                residence['address'] = card.find('p', class_='fr-card__desc').get_text(strip=True) if card.find('p', class_='fr-card__desc') else 'N/A'
                details = card.find_all('p', class_='fr-card__detail')
                residence['details'] = " | ".join([d.get_text(strip=True) for d in details])
                data.append(residence)
        return data
    except requests.exceptions.RequestException as e:
        print(f"Error fetching page: {e}")
        return None

def read_csv_to_list(filepath):
    """Reads a CSV file into a list of dictionaries."""
    if not os.path.exists(filepath): return []
    with open(filepath, mode='r', newline='', encoding='utf-8') as f:
        return [row for row in csv.DictReader(f)]

def write_list_to_csv(filepath, data_list, headers):
    """Writes a list of dictionaries to a CSV file."""
    with open(filepath, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data_list)

def append_list_to_csv(filepath, data_list, headers):
    """Appends a list of dictionaries to a CSV file."""
    file_exists = os.path.exists(filepath)
    with open(filepath, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        if not file_exists: writer.writeheader()
        writer.writerows(data_list)

def make_dict_hashable(d):
    """Converts a dictionary to a hashable frozenset for comparison."""
    return frozenset(d.items())

def get_price_as_int(residence):
    """Extracts the integer value from a price string, handling various formats."""
    price_str = residence.get('price', '0')
    try:
        # Remove currency symbols and whitespace
        price_clean = price_str.replace('€', '').strip()
        # Handle price ranges by taking the first number found
        if 'à' in price_clean:
            price_clean = price_clean.split('à')[0].strip()
        # Remove any remaining non-numeric characters (like 'de ')
        price_clean = ''.join(filter(lambda x: x.isdigit() or x in [',', '.'], price_clean))
        # Replace comma with dot for float conversion
        price_clean = price_clean.replace(',', '.')
        return int(float(price_clean))
    except (ValueError, TypeError, AttributeError):
        print(f"Warning: Could not parse price '{price_str}', sorting to end")
        return 9999

# --- Email and Reporting Functions ---

def format_residence_html(residence, color="black"):
    """Formats a single residence into an HTML block."""
    return f"""
    <div style="border-left: 3px solid {color}; padding-left: 15px; margin-bottom: 20px;">
        <p style="margin:0; font-size: 18px; font-weight: bold;">
            <a href="{residence['link']}" style="color: {color}; text-decoration: none;">{residence['name']}</a>
        </p>
        <p style="margin:0; font-size: 16px; color: #333;"><b>Prix:</b> {residence['price']}</p>
        <p style="margin:0; font-size: 14px; color: #555;">{residence['address']}</p>
        <p style="margin:0; font-size: 14px; color: #555;"><i>{residence['details']}</i></p>
    </div>
    """

def create_alert_email_body(title, added, removed, all_available):
    """Builds the HTML for an immediate alert email (changes first)."""
    html = f"""
    <html><head><style>body {{font-family: Arial, sans-serif; color: #333;}}</style></head>
    <body style="margin: 0; padding: 0;">
    <div style="max-width: 700px; margin: 20px auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px; background-color: #f9f9f9;">
        <h1 style="font-size: 24px; color: #00549F; border-bottom: 2px solid #eee; padding-bottom: 10px;">{title}</h1>"""
    if added:
        html += f'<h2 style="font-size: 20px; color: #28a745;">Nouvelles résidences disponibles ({len(added)})</h2>'
        for res in added: html += format_residence_html(res, color="#28a745")
    if removed:
        html += f'<h2 style="font-size: 20px; color: #dc3545;">Résidences qui ne sont plus listées ({len(removed)})</h2>'
        for res in removed: html += format_residence_html(res, color="#dc3545")
    
    html += f'<h2 style="font-size: 20px; border-top: 2px solid #eee; padding-top: 20px;">Liste complète des résidences disponibles ({len(all_available)})</h2>'
    if all_available:
        for res in all_available: html += format_residence_html(res)
    else:
        html += "<p>Il n'y a actuellement aucune résidence disponible.</p>"
    html += "</div></body></html>"
    return html

def create_summary_email_body(title, added, removed, all_available):
    """Builds the HTML for the daily summary email (full list first)."""
    html = f"""
    <html><head><style>body {{font-family: Arial, sans-serif; color: #333;}}</style></head>
    <body style="margin: 0; padding: 0;">
    <div style="max-width: 700px; margin: 20px auto; padding: 20px; border: 1px solid #ddd; border-radius: 8px; background-color: #f9f9f9;">
        <h1 style="font-size: 24px; color: #00549F; border-bottom: 2px solid #eee; padding-bottom: 10px;">{title}</h1>"""
    
    html += f'<h2 style="font-size: 20px;">Liste complète des résidences disponibles en fin de journée ({len(all_available)})</h2>'
    if all_available:
        for res in all_available: html += format_residence_html(res)
    else:
        html += "<p>Il n'y a actuellement aucune résidence disponible.</p>"

    if not added and not removed:
        html += '<p style="font-size: 16px; border-top: 2px solid #eee; margin-top: 20px; padding-top: 20px;">Aucun changement de disponibilité n\'a été détecté aujourd\'hui.</p>'
    if added:
        html += f'<h2 style="font-size: 20px; color: #28a745; border-top: 2px solid #eee; margin-top: 20px; padding-top: 20px;">Ajouté(s) aujourd\'hui ({len(added)})</h2>'
        for res in added: html += format_residence_html(res, color="#28a745")
    if removed:
        html += f'<h2 style="font-size: 20px; color: #dc3545; border-top: 2px solid #eee; margin-top: 20px; padding-top: 20px;">Retiré(s) aujourd\'hui ({len(removed)})</h2>'
        for res in removed: html += format_residence_html(res, color="#dc3545")

    html += "</div></body></html>"
    return html

def send_email(subject, html_body):
    """Sends the email using SendGrid."""
    if not all([SENDGRID_API_KEY, TO_EMAIL, FROM_EMAIL]):
        print("Email credentials not found. Cannot send email.")
        return
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{SENDER_NAME} <{FROM_EMAIL}>"
    msg['To'] = TO_EMAIL
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    try:
        with smtplib.SMTP("smtp.sendgrid.net", 587) as server:
            server.starttls()
            server.login("apikey", SENDGRID_API_KEY)
            server.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())
        print(f"Email with subject '{subject}' sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

# --- Main Execution Logic ---
if __name__ == "__main__":
    now_cet = datetime.now(CET)
    today_str = now_cet.strftime('%Y-%m-%d')
    
    # --- Part 1: Immediate Change Detection and Notification ---
    print("--- Running Hourly Change Detection ---")
    current_residences = scrape_crous_page(URL)
    if current_residences is not None:
        previous_residences = read_csv_to_list(AVAILABLE_CSV)
        current_set = {make_dict_hashable(d) for d in current_residences}
        previous_set = {make_dict_hashable(d) for d in previous_residences}

        if current_set != previous_set:
            print("Change detected!")
            added_list = [dict(h) for h in (current_set - previous_set)]
            removed_list = [dict(h) for h in (previous_set - current_set)]

            # A. Sort all lists by price before sending
            added_list.sort(key=get_price_as_int)
            removed_list.sort(key=get_price_as_int)
            current_residences.sort(key=get_price_as_int)

            # B. Send immediate email update
            num_added = len(added_list)
            num_removed = len(removed_list)
            subject = "Alerte CROUS Bot: Changement de disponibilité !" # Default
            if num_added > 0 and num_removed > 0:
                added_str = f"+{num_added} ajoutée" if num_added == 1 else f"+{num_added} ajoutées"
                removed_str = f"-{num_removed} retirée" if num_removed == 1 else f"-{num_removed} retirées"
                subject = f"Alerte CROUS Bot: {added_str}, {removed_str}"
            elif num_added > 0:
                subject = f"Alerte CROUS Bot (+): {num_added} nouvelle{'s' if num_added > 1 else ''} résidence{'s' if num_added > 1 else ''} disponible{'s' if num_added > 1 else ''} !"
            elif num_removed > 0:
                subject = f"Alerte CROUS Bot (-): {num_removed} résidence{'s ne sont' if num_removed > 1 else ' n est'} plus disponible{'s' if num_removed > 1 else ''}"

            email_body = create_alert_email_body("Alerte Immédiate", added_list, removed_list, current_residences)
            send_email(subject, email_body)

            # C. Log changes for the daily summary
            activity_log_headers = ['timestamp_cet', 'status', 'name', 'price', 'address', 'details', 'link']
            activity_to_log = []
            for item in added_list:
                log_item = item.copy()
                log_item.update({'timestamp_cet': now_cet.isoformat(), 'status': 'added'})
                activity_to_log.append(log_item)
            for item in removed_list:
                log_item = item.copy()
                log_item.update({'timestamp_cet': now_cet.isoformat(), 'status': 'removed'})
                activity_to_log.append(log_item)
            if activity_to_log:
                append_list_to_csv(DAILY_ACTIVITY_LOG_CSV, activity_to_log, activity_log_headers)

            # D. Update the master state file
            headers = ['name', 'price', 'address', 'details', 'link']
            write_list_to_csv(AVAILABLE_CSV, current_residences, headers)
            
            # E. Update the master removed log
            if removed_list:
                timestamp = now_cet.strftime("%Y-%m-%d %H:%M:%S %Z")
                for item in removed_list:
                    item['removed_timestamp'] = timestamp
                removed_headers = headers + ['removed_timestamp']
                append_list_to_csv(REMOVED_LOG_CSV, removed_list, removed_headers)
        else:
            print("No changes detected since last run.")

    # --- Part 2: Comprehensive Daily Summary Report ---
    print("\n--- Checking for Daily Summary Report ---")
    is_report_time = (now_cet.hour == 22) or (now_cet.hour == 23 and now_cet.minute <= 30)
    report_log = read_csv_to_list(REPORT_LOG_CSV)
    sent_today = any(log.get('sent_date') == today_str for log in report_log)

    if is_report_time and not sent_today:
        print(f"It's daily report time in CET ({now_cet.strftime('%H:%M:%S')}) and the report has not been sent. Generating summary...")
        
        # A. Get all activity logged today
        full_activity_log = read_csv_to_list(DAILY_ACTIVITY_LOG_CSV)
        today_activity = [row for row in full_activity_log if row.get('timestamp_cet', '').startswith(today_str)]
        
        total_added_today = [row for row in today_activity if row.get('status') == 'added']
        total_removed_today = [row for row in today_activity if row.get('status') == 'removed']

        # B. Scrape the final list for the report
        final_residences = scrape_crous_page(URL)
        if final_residences is None:
            final_residences = read_csv_to_list(AVAILABLE_CSV) # Fallback to last known good state

        # C. Sort all lists by price before sending
        total_added_today.sort(key=get_price_as_int)
        total_removed_today.sort(key=get_price_as_int)
        final_residences.sort(key=get_price_as_int)

        # D. Create and send the comprehensive email
        subject = "Daily CROUS BOT Report"
        email_body = create_summary_email_body(f"Rapport du {now_cet.strftime('%Y-%m-%d %H:%M')}", total_added_today, total_removed_today, final_residences)
        send_email(subject, email_body)
        
        # E. Log that the report was sent
        log_entry = [{'sent_date': today_str, 'sent_time_cet': now_cet.strftime('%H:%M:%S')}]
        append_list_to_csv(REPORT_LOG_CSV, log_entry, headers=['sent_date', 'sent_time_cet'])
    else:
        if is_report_time and sent_today:
             print(f"It's daily report time, but the summary has already been sent today.")
        else:
             print(f"Not currently in the daily report window ({now_cet.strftime('%H:%M:%S')} CET).")
             
    print("\nScript finished.")