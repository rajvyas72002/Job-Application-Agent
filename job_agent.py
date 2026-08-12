"""
Job Application Agent
----------------------
Pulls jobs from Adzuna API across MULTIPLE cities in one click, drafts a
personalized email using local Ollama, auto-fills + auto-submits known ATS
application forms (Greenhouse / Lever / Workday) via Playwright, sends a
plain email where only an address is available, logs everything to SQLite,
and pings you on Telegram after every action so you know what happened
without watching the screen.

WARNING: auto-submit means Playwright clicks the real Submit button with
NO human review step. Only enable this once you trust the drafted content.
You can flip AUTO_SUBMIT to False below at any time to go back to
fill-only (review before you click submit yourself).

Setup before running:
1. pip install streamlit requests playwright --break-system-packages
   playwright install chromium
2. Install Ollama (ollama.com), then run: ollama pull llama3.1
3. Get free Adzuna app_id + app_key: https://developer.adzuna.com/
4. Get a Gmail App Password: Google Account -> Security -> App Passwords
5. Get a Telegram bot token from @BotFather and your chat id from
   @userinfobot (both inside the Telegram app)

Run with: streamlit run job_agent.py
"""

import streamlit as st
import requests
import smtplib
import sqlite3
import json
from email.mime.text import MIMEText
from datetime import datetime

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Set to False if you want Playwright to fill the form and stop before
# clicking Submit, so you can eyeball it first. True = fully automatic.
AUTO_SUBMIT = True

# ---------------------------------------------------------------------------
# CONFIG - edit these or fill them in through the sidebar in the app
# ---------------------------------------------------------------------------
DB_PATH = "job_agent.db"
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"
ADZUNA_COUNTRY = "in"  # India

YOUR_SUMMARY = """
Raj Vyas - Python Developer and Forward Deployed Engineer at Genus Power Infrastructures Ltd.
Experience building internal automation tools, data pipelines, and dashboards for smart meter
infrastructure. Strong hands-on experience in computer vision: YOLO based object detection and
tracking, real-time vehicle detection and license plate recognition, multi-camera 3D trajectory
systems. Comfortable with LLM integration (Claude, ChatGPT, Ollama) for automation and reporting.
Stack: Python, PyTorch, OpenCV, YOLO, Streamlit, FastAPI, Pandas, SQLite, Docker, AWS.
Pursuing MCA in AI/ML. National-level badminton athlete.
GitHub: github.com/RAJVYAS72002 | LinkedIn: linkedin.com/in/vyasr3208
"""

# ---------------------------------------------------------------------------
# DATABASE
# ---------------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS applications (
            id TEXT PRIMARY KEY,
            company TEXT,
            role TEXT,
            location TEXT,
            apply_link TEXT,
            email_used TEXT,
            status TEXT,
            drafted_body TEXT,
            sent_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def already_seen(job_id):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT id FROM applications WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return row is not None

def save_job(job_id, company, role, location, apply_link, status, email_used="", drafted_body="", sent_at=""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR REPLACE INTO applications
        (id, company, role, location, apply_link, email_used, status, drafted_body, sent_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, company, role, location, apply_link, email_used, status, drafted_body, sent_at))
    conn.commit()
    conn.close()

def get_all_jobs():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, company, role, location, apply_link, status, sent_at FROM applications ORDER BY sent_at DESC").fetchall()
    conn.close()
    return rows

# ---------------------------------------------------------------------------
# ADZUNA - pull jobs
# ---------------------------------------------------------------------------
def fetch_jobs(app_id, app_key, keyword, location="", results=20):
    url = f"https://api.adzuna.com/v1/api/jobs/{ADZUNA_COUNTRY}/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": keyword,
        "where": location,
        "results_per_page": results,
        "content-type": "application/json",
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for item in data.get("results", []):
        jobs.append({
            "id": item.get("id"),
            "company": item.get("company", {}).get("display_name", "Unknown"),
            "role": item.get("title", "Unknown Role"),
            "location": item.get("location", {}).get("display_name", ""),
            "description": item.get("description", ""),
            "apply_link": item.get("redirect_url", ""),
        })
    return jobs

def fetch_jobs_multi_city(app_id, app_key, keyword, cities_csv, results_per_city=20):
    """
    Takes a comma separated string like 'Pune, Jaipur, Bangalore, Ahmedabad'
    and loops fetch_jobs for each, deduplicating by job id across cities.
    """
    cities = [c.strip() for c in cities_csv.split(",") if c.strip()]
    if not cities:
        cities = [""]  # no location filter, national search

    all_jobs = {}
    for city in cities:
        try:
            jobs = fetch_jobs(app_id, app_key, keyword, city, results_per_city)
            for j in jobs:
                all_jobs[j["id"]] = j  # dict keyed by id auto-dedupes
        except Exception as e:
            st.warning(f"Fetch failed for {city or 'national'}: {e}")
    return list(all_jobs.values())

# ---------------------------------------------------------------------------
# OLLAMA - draft the email
# ---------------------------------------------------------------------------
def ask_ollama(prompt, model=OLLAMA_MODEL):
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except Exception as e:
        return f"[Ollama error: {e}. Is 'ollama serve' running?]"

def generate_email(company, role, job_description, your_summary):
    prompt = f"""
Write a short, professional cold email applying for the role of "{role}" at "{company}".

Candidate background:
{your_summary}

Job description snippet (use only relevant parts, do not repeat it verbatim):
{job_description[:600]}

Rules:
- Under 150 words
- No generic filler like "I am writing to express my interest"
- Mention one specific relevant project
- End with a clear call to action (asking for a call or next step)
- Plain text, no markdown, no subject line, just the email body
"""
    return ask_ollama(prompt)

# ---------------------------------------------------------------------------
# TELEGRAM NOTIFICATIONS
# ---------------------------------------------------------------------------
def notify_telegram(bot_token, chat_id, message):
    if not bot_token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=10,
        )
    except Exception:
        pass  # notification failing should never crash the main flow

# ---------------------------------------------------------------------------
# PLAYWRIGHT - best-effort auto-fill + auto-submit for known ATS platforms
# ---------------------------------------------------------------------------
def detect_ats(url):
    url = url.lower()
    if "greenhouse.io" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "myworkdayjobs.com" in url or "workday.com" in url:
        return "workday"
    return None

def autofill_application(apply_link, full_name, email, phone, cover_note, resume_path=None):
    """
    Best-effort form fill for Greenhouse / Lever / Workday.
    Returns (success: bool, message: str).
    Custom/unknown ATS platforms are skipped, not guessed at blind,
    since guessing wrong field mappings risks submitting garbage.
    """
    if not PLAYWRIGHT_AVAILABLE:
        return False, "Playwright not installed. Run: pip install playwright --break-system-packages && playwright install chromium"

    ats = detect_ats(apply_link)
    if ats is None:
        return False, "Unknown application form (not Greenhouse/Lever/Workday). Skipped, apply manually."

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(apply_link, timeout=30000)
            page.wait_for_load_state("networkidle", timeout=15000)

            if ats == "greenhouse":
                _fill_by_label(page, ["First Name"], full_name.split(" ")[0])
                _fill_by_label(page, ["Last Name"], full_name.split(" ")[-1])
                _fill_by_label(page, ["Email"], email)
                _fill_by_label(page, ["Phone"], phone)
                _fill_by_label(page, ["Cover Letter", "Additional Information"], cover_note)
                if resume_path:
                    _upload_resume(page, resume_path)

            elif ats == "lever":
                _fill_by_name(page, "name", full_name)
                _fill_by_name(page, "email", email)
                _fill_by_name(page, "phone", phone)
                _fill_by_label(page, ["Additional Information"], cover_note)
                if resume_path:
                    _upload_resume(page, resume_path)

            elif ats == "workday":
                # Workday forms are heavily multi-step and account-gated.
                # Best-effort only: fill what's visible on the first screen.
                _fill_by_label(page, ["First Name"], full_name.split(" ")[0])
                _fill_by_label(page, ["Last Name"], full_name.split(" ")[-1])
                _fill_by_label(page, ["Email"], email)
                _fill_by_label(page, ["Phone"], phone)

            if AUTO_SUBMIT:
                submit_btn = page.locator("button:has-text('Submit')").first
                if submit_btn.count() > 0:
                    submit_btn.click()
                    page.wait_for_timeout(3000)
                    browser.close()
                    return True, f"Filled and submitted via {ats}."
                else:
                    browser.close()
                    return False, f"Filled on {ats} but could not find a Submit button. Check manually: {apply_link}"
            else:
                browser.close()
                return True, f"Filled on {ats}, left open for manual review/submit: {apply_link}"

    except Exception as e:
        return False, f"Playwright error on {ats}: {e}"

def _fill_by_label(page, label_options, value):
    for label in label_options:
        try:
            field = page.get_by_label(label, exact=False).first
            if field.count() > 0:
                field.fill(value)
                return True
        except Exception:
            continue
    return False

def _fill_by_name(page, name_attr, value):
    try:
        field = page.locator(f"input[name='{name_attr}'], textarea[name='{name_attr}']").first
        if field.count() > 0:
            field.fill(value)
            return True
    except Exception:
        pass
    return False

def _upload_resume(page, resume_path):
    try:
        file_input = page.locator("input[type='file']").first
        if file_input.count() > 0:
            file_input.set_input_files(resume_path)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# EMAIL SENDING
# ---------------------------------------------------------------------------
def send_email(to_address, subject, body, from_address, app_password):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_address
    msg["To"] = to_address
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(from_address, app_password)
        server.sendmail(from_address, to_address, msg.as_string())

# ---------------------------------------------------------------------------
# STREAMLIT APP
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Job Application Agent", layout="wide")
init_db()

st.title("Job Application Agent")
st.caption("Pull jobs -> draft with Ollama -> review -> send -> log. Nothing sends without your approval.")

with st.sidebar:
    st.header("Credentials")
    adzuna_id = st.text_input("Adzuna App ID", type="password")
    adzuna_key = st.text_input("Adzuna App Key", type="password")
    st.divider()
    from_address = st.text_input("Your Gmail address")
    app_password = st.text_input("Gmail App Password", type="password")
    st.divider()
    ollama_model = st.text_input("Ollama model", value=OLLAMA_MODEL)
    st.divider()
    st.header("Telegram notifications")
    tg_token = st.text_input("Bot token", type="password")
    tg_chat_id = st.text_input("Your chat ID")
    st.divider()
    st.header("Your details (for auto-apply forms)")
    your_full_name = st.text_input("Full name", value="Raj Vyas")
    your_phone = st.text_input("Phone", value="+91 9784996231")
    resume_path = st.text_input("Resume file path on this PC (for upload)", value="")
    st.divider()
    auto_submit_toggle = st.checkbox("Auto-submit forms (no review)", value=AUTO_SUBMIT)
    AUTO_SUBMIT = auto_submit_toggle

tab1, tab2 = st.tabs(["Find & Draft", "Sent Log"])

with tab1:
    col1, col2, col3 = st.columns(3)
    with col1:
        keyword = st.text_input("Role keyword", value="Forward Deployed Engineer")
    with col2:
        cities_csv = st.text_input("Cities (comma separated)", value="Pune, Jaipur, Bangalore, Ahmedabad")
    with col3:
        num_results = st.number_input("Jobs per city", min_value=5, max_value=50, value=15)

    if st.button("Fetch jobs from all cities", type="primary"):
        if not adzuna_id or not adzuna_key:
            st.error("Enter your Adzuna App ID and App Key in the sidebar first.")
        else:
            with st.spinner(f"Fetching jobs across: {cities_csv} ..."):
                jobs = fetch_jobs_multi_city(adzuna_id, adzuna_key, keyword, cities_csv, num_results)
            new_jobs = [j for j in jobs if not already_seen(j["id"])]
            st.session_state["jobs"] = new_jobs
            st.success(f"Found {len(jobs)} jobs across all cities, {len(new_jobs)} new.")
            notify_telegram(tg_token, tg_chat_id, f"Job fetch done: {len(new_jobs)} new jobs found for '{keyword}' across {cities_csv}.")

    jobs = st.session_state.get("jobs", [])

    for job in jobs:
        with st.expander(f"{job['role']} — {job['company']} ({job['location']})"):
            st.write(job["description"][:400] + "...")
            st.write(f"Apply link: {job['apply_link']}")

            draft_key = f"draft_{job['id']}"
            if st.button("Draft email with Ollama", key=f"btn_{job['id']}"):
                with st.spinner("Ollama is writing..."):
                    draft = generate_email(job["company"], job["role"], job["description"], YOUR_SUMMARY)
                st.session_state[draft_key] = draft

            if draft_key in st.session_state:
                edited = st.text_area("Review and edit before sending", value=st.session_state[draft_key], height=200, key=f"edit_{job['id']}")
                to_email = st.text_input("Send to email (if known, otherwise use apply link manually)", key=f"email_{job['id']}")

                col_a, col_b, col_c, col_d = st.columns(4)

                with col_a:
                    if st.button("Auto-apply via form", key=f"autoapply_{job['id']}"):
                        with st.spinner(f"Playwright working on {job['apply_link']} ..."):
                            success, msg = autofill_application(
                                job["apply_link"], your_full_name, from_address or "you@example.com",
                                your_phone, edited, resume_path or None
                            )
                        status = "auto_applied" if success else "auto_apply_failed"
                        save_job(job["id"], job["company"], job["role"], job["location"],
                                  job["apply_link"], status, "", edited, datetime.now().isoformat())
                        if success:
                            st.success(msg)
                        else:
                            st.warning(msg)
                        notify_telegram(
                            tg_token, tg_chat_id,
                            f"{'✅' if success else '⚠️'} {job['role']} @ {job['company']}\n{msg}"
                        )

                with col_b:
                    if st.button("Send email", key=f"send_{job['id']}"):
                        if not to_email:
                            st.error("Enter a recipient email address.")
                        elif not from_address or not app_password:
                            st.error("Fill in your Gmail address and App Password in the sidebar.")
                        else:
                            try:
                                send_email(
                                    to_email,
                                    f"Application for {job['role']} at {job['company']}",
                                    edited,
                                    from_address,
                                    app_password,
                                )
                                save_job(job["id"], job["company"], job["role"], job["location"],
                                          job["apply_link"], "sent", to_email, edited,
                                          datetime.now().isoformat())
                                st.success("Sent and logged.")
                                notify_telegram(tg_token, tg_chat_id, f"✅ Emailed {job['company']} for {job['role']}.")
                            except Exception as e:
                                st.error(f"Send failed: {e}")

                with col_c:
                    if st.button("Mark as applied manually", key=f"manual_{job['id']}"):
                        save_job(job["id"], job["company"], job["role"], job["location"],
                                  job["apply_link"], "applied_manually", "", edited,
                                  datetime.now().isoformat())
                        st.success("Logged as applied manually.")

                with col_d:
                    if st.button("Skip this job", key=f"skip_{job['id']}"):
                        save_job(job["id"], job["company"], job["role"], job["location"],
                                  job["apply_link"], "skipped", "", "", datetime.now().isoformat())
                        st.info("Marked as skipped, won't show again.")

with tab2:
    st.subheader("Application history")
    rows = get_all_jobs()
    if rows:
        st.table(
            [{"Company": r[1], "Role": r[2], "Location": r[3], "Status": r[5], "When": r[6]} for r in rows]
        )
    else:
        st.write("Nothing logged yet.")