# Final Crous Notifier Script
# Re-architected to handle multiple, independently configured monitoring targets.
# Includes both immediate change notifications AND a comprehensive daily summary report.
# --- NOW WITH PAGINATION SUPPORT ---

import requests
from bs4 import BeautifulSoup
import csv
import os
from datetime import datetime, timezone, timedelta
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
import pprint
import math
import re

# --- Global Configuration ---
# PRODUCTION: Email configuration is read from environment variables
BREVO_LOGIN = os.environ.get("BREVO_LOGIN")
BREVO_API_KEY = os.environ.get("BREVO_API_KEY") # Brevo's SMTP Key acts as the password
TO_EMAIL = os.environ.get("TO_EMAIL")
FROM_EMAIL = os.environ.get("FROM_EMAIL") # This must be a verified sender in your Brevo account
SENDER_NAME = "CROUS BOT Notifier"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
CET = timezone(timedelta(hours=1))

# --- Core Scraping and File Functions ---

def scrape_crous_page(url):
    """
    Scrapes a Crous page and all its paginations, returning a single list of all residences.
    """
    all_residences = []
    
    try:
        # --- MODIFIED: Fetch the original, unmodified URL first (this is page 1) ---
        print(f"Fetching page 1 to determine total pages for: {url}")
        response = requests.get(url, headers=HEADERS)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        total_pages = 1
        results_h2 = soup.find('h2', class_='SearchResults-desktop')
        if results_h2:
            results_text = results_h2.get_text()
            match = re.search(r'(\d+)\s+logement', results_text)
            if match:
                total_residences = int(match.group(1))
                # The website shows 24 residences per page
                total_pages = math.ceil(total_residences / 24)
                print(f"Found {total_residences} total residences across {int(total_pages)} page(s).")
            else:
                print("No residence count found in header. Assuming 1 page.")
        else:
            print("Could not find results header. Assuming 1 page.")

        # The first page's soup is already loaded, so we process it.
        cards = soup.find_all('div', class_='fr-card')
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
                all_residences.append(residence)

        # Now, loop through the rest of the pages if they exist
        if total_pages > 1:
            # Clean any existing page param from the base URL to construct subsequent page URLs
            base_url = re.sub(r'[?&]page=\d+', '', url)
            
            for page_num in range(2, int(total_pages) + 1):
                page_url = base_url
                if '?' in page_url:
                    # Append with '&' if other parameters already exist
                    if not page_url.endswith('&'): page_url += '&'
                    page_url += f'page={page_num}'
                else:
                    # Append with '?' if it's the first parameter
                    page_url += f'?page={page_num}'
                
                print(f"Fetching page {page_num}...")
                response = requests.get(page_url, headers=HEADERS)
                response.raise_for_status()
                soup = BeautifulSoup(response.content, 'html.parser')
                
                page_cards = soup.find_all('div', class_='fr-card')
                for card in page_cards:
                    residence = {}
                    title_element = card.find('h3', class_='fr-card__title')
                    if title_element and title_element.find('a'):
                        residence['name'] = title_element.find('a').get_text(strip=True)
                        residence['link'] = "https://trouverunlogement.lescrous.fr" + title_element.find('a')['href']
                        residence['price'] = card.find('p', class_='fr-badge').get_text(strip=True) if card.find('p', class_='fr-badge') else 'N/A'
                        residence['address'] = card.find('p', class_='fr-card__desc').get_text(strip=True) if card.find('p', class_='fr-card__desc') else 'N/A'
                        details = card.find_all('p', class_='fr-card__detail')
                        residence['details'] = " | ".join([d.get_text(strip=True) for d in details])
                        all_residences.append(residence)
        
        print(f"Total residences scraped from all pages: {len(all_residences)}")
        return all_residences

    except requests.exceptions.RequestException as e:
        print(f"Error during scraping process for {url}: {e}")
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
        price_clean = price_str.replace('€', '').strip()
        if 'à' in price_clean:
            price_clean = price_clean.split('à')[0].strip()
        price_clean = ''.join(filter(lambda x: x.isdigit() or x in [',', '.'], price_clean))
        price_clean = price_clean.replace(',', '.')
        return int(float(price_clean))
    except (ValueError, TypeError, AttributeError):
        print(f"Warning: Could not parse price '{price_str}', sorting to end")
        return 9999

def plural(n, singular, plural_form):
    """Returns the singular or plural form of a word based on the count n."""
    return singular if n == 1 else plural_form

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
    """Sends the email using Brevo's SMTP."""
    if not all([BREVO_LOGIN, BREVO_API_KEY, TO_EMAIL, FROM_EMAIL]):
        print("Email credentials not found. Cannot send email.")
        return
    msg = MIMEMultipart('alternative')
    msg['Subject'] = Header(subject, 'utf-8')
    msg['From'] = f"{SENDER_NAME} <{FROM_EMAIL}>"
    msg['To'] = TO_EMAIL
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))
    try:
        with smtplib.SMTP("smtp-relay.brevo.com", 587) as server:
            server.starttls()
            server.login(BREVO_LOGIN, BREVO_API_KEY)
            server.sendmail(FROM_EMAIL, TO_EMAIL, msg.as_string())
        print(f"Email with subject '{subject}' sent successfully!")
    except Exception as e:
        print(f"Failed to send email: {e}")

# --- Main processing function for a single target ---
def process_target(target_config):
    """
    Runs the entire notification logic for a single configured target URL.
    """
    url = target_config["url"]
    folder = target_config["folder_name"]
    send_alert = target_config["send_immediate_alert"]
    send_summary = target_config["send_daily_report"]
    
    if folder != ".":
        os.makedirs(folder, exist_ok=True)

    available_csv = os.path.join(folder, 'available_residences.csv')
    removed_log_csv = os.path.join(folder, 'removed_residences.log.csv')
    activity_log_csv = os.path.join(folder, 'daily_activity_log.csv')
    report_log_csv = os.path.join(folder, 'report_log.csv')

    now_cet = datetime.now(CET)
    today_str = now_cet.strftime('%Y-%m-%d')
    
    # --- Part 1: Immediate Change Detection and Notification ---
    print(f"--- Running Hourly Change Detection for '{folder}' ---")
    current_residences = scrape_crous_page(url)
    if current_residences is not None:
        previous_residences = read_csv_to_list(available_csv)
        current_set = {make_dict_hashable(d) for d in current_residences}
        previous_set = {make_dict_hashable(d) for d in previous_residences}

        if current_set != previous_set:
            print("Change detected!")
            added_list = [dict(h) for h in (current_set - previous_set)]
            removed_list = [dict(h) for h in (previous_set - current_set)]

            added_list.sort(key=get_price_as_int)
            removed_list.sort(key=get_price_as_int)
            current_residences.sort(key=get_price_as_int)

            if send_alert:
                num_added = len(added_list)
                num_removed = len(removed_list)
                subject = f"Alerte CROUS Bot ({folder}): Changement de disponibilité !"
                if num_added > 0 and num_removed > 0:
                    added_str = f"+{num_added} {plural(num_added, 'ajoutée', 'ajoutées')}"
                    removed_str = f"-{num_removed} {plural(num_removed, 'retirée', 'retirées')}"
                    subject = f"Alerte CROUS Bot : {added_str}, {removed_str}"
                elif num_added > 0:
                    subject = f"Alerte CROUS Bot (+): {num_added} nouvelle{plural(num_added, '', 's')} résidence{plural(num_added, '', 's')} disponible{plural(num_added, '', 's')} !"
                elif num_removed > 0:
                    subject = f"Alerte CROUS Bot (-): {num_removed} résidence{plural(num_removed, '', 's')} {plural(num_removed, 'n’est', 'ne sont')} plus disponible{plural(num_removed, '', 's')}"
                
                email_body = create_alert_email_body("Changement de disponibilité !", added_list, removed_list, current_residences)
                send_email(subject, email_body)
            else:
                print("Immediate alert is disabled for this target. Skipping email.")

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
                append_list_to_csv(activity_log_csv, activity_to_log, activity_log_headers)

            headers = ['name', 'price', 'address', 'details', 'link']
            write_list_to_csv(available_csv, current_residences, headers)
            
            if removed_list:
                timestamp = now_cet.strftime("%Y-%m-%d %H:%M:%S %Z")
                for item in removed_list:
                    item['removed_timestamp'] = timestamp
                removed_headers = headers + ['removed_timestamp']
                append_list_to_csv(removed_log_csv, removed_list, removed_headers)
        else:
            print("No changes detected since last run.")

    # --- Part 2: Comprehensive Daily Summary Report ---
    print(f"\n--- Checking for Daily Summary Report for '{folder}' ---")
    is_report_time = (now_cet.hour == 22) or (now_cet.hour == 23 and now_cet.minute <= 30)
    report_log = read_csv_to_list(report_log_csv)
    sent_today = any(log.get('sent_date') == today_str for log in report_log)

    if is_report_time and not sent_today and send_summary:
        print(f"It's daily report time in CET and report is enabled. Generating summary...")
        
        full_activity_log = read_csv_to_list(activity_log_csv)
        today_activity = [row for row in full_activity_log if row.get('timestamp_cet', '').startswith(today_str)]
        total_added_today = [row for row in today_activity if row.get('status') == 'added']
        total_removed_today = [row for row in today_activity if row.get('status') == 'removed']

        final_residences = scrape_crous_page(url)
        if final_residences is None:
            final_residences = read_csv_to_list(available_csv)

        total_added_today.sort(key=get_price_as_int)
        total_removed_today.sort(key=get_price_as_int)
        final_residences.sort(key=get_price_as_int)

        subject = f"Rapport CROUS Bot du {today_str} ({folder})"
        email_body = create_summary_email_body(f"Rapport du {today_str} ({folder})", total_added_today, total_removed_today, final_residences)
        send_email(subject, email_body)
        
        log_entry = [{'sent_date': today_str, 'sent_time_cet': now_cet.strftime('%H:%M:%S')}]
        append_list_to_csv(report_log_csv, log_entry, headers=['sent_date', 'sent_time_cet'])
    else:
        if is_report_time and not send_summary:
            print("It's report time, but daily summary is disabled for this target.")
        elif is_report_time and sent_today:
            print("It's daily report time, but the summary has already been sent today for this target.")
        else:
            print(f"Not currently in the daily report window ({now_cet.strftime('%H:%M:%S')} CET).")
             
    print(f"\nProcessing for '{folder}' finished.")


# --- Main Execution Block ---
if __name__ == "__main__":
    # --- Define all monitoring targets here ---
    TARGETS_TO_MONITOR = [
        {
            "url": "https://trouverunlogement.lescrous.fr/tools/41/search",
            "folder_name": ".",
            "send_immediate_alert": True,
            "send_daily_report": True
        },
        {
            "url": "https://trouverunlogement.lescrous.fr/tools/41/search?bounds=-0.6386987_44.9161806_-0.5336838_44.8107826",
            "folder_name": "data_bordeaux",
            "send_immediate_alert": True,
            "send_daily_report": True
        }
    ]

    # Loop through each target and process it
    for target in TARGETS_TO_MONITOR:
        process_target(target)
