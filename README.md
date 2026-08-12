# Job Application Agent

A Streamlit app that automates the repetitive parts of job hunting — pulling live openings from Adzuna across multiple cities, drafting personalized emails with a local LLM (Ollama), auto-filling known ATS application forms via Playwright, and logging every application to a local database.

Nothing sends without your review by default.

## Features

- **Multi-city job search** — search one keyword across several cities at once, auto-deduplicated
- **AI-drafted emails** — generates a short, personalized cold email per job using a local Ollama model (no API costs, fully offline)
- **Auto-fill known ATS forms** — detects Greenhouse, Lever, and Workday application pages and fills them via Playwright
- **Email sending** — sends applications directly via Gmail when a recruiter email is known
- **Application tracking** — every action (sent, applied, skipped, failed) is logged to a local SQLite database
- **Telegram notifications** — optional pings after every action so you don't have to babysit the screen

## Setup

### 1. Install dependencies
```bash
pip install streamlit requests playwright --break-system-packages
playwright install chromium
```

### 2. Install Ollama
Download from [ollama.com](https://ollama.com), then pull a model:
```bash
ollama pull llama3.2
```
> Use a smaller model like `llama3.2` (~2GB) if you're on a GPU with limited VRAM (4GB or less). Larger models like `llama3.1` (8B) need 5–6GB+ to load.

Start the Ollama server before running the app:
```bash
ollama serve
```

### 3. Get an Adzuna API key
Free app ID + key: [developer.adzuna.com](https://developer.adzuna.com/)

### 4. Get a Gmail App Password
Google Account → Security → App Passwords (needed to send email applications)

### 5. (Optional) Set up Telegram notifications
Get a bot token from [@BotFather](https://t.me/BotFather) and your chat ID from [@userinfobot](https://t.me/userinfobot).

## Running the app

```bash
streamlit run job_agent.py
```

Enter your credentials in the sidebar, then use the **Find & Draft** tab to search for jobs and process them.

## ⚠️ Auto-Submit Warning

The `AUTO_SUBMIT` flag (default: `True`, also toggleable from the sidebar) controls whether Playwright clicks the real **Submit** button on detected ATS forms with **no human review step**.

- `AUTO_SUBMIT = True` → form is filled and submitted automatically
- `AUTO_SUBMIT = False` → form is filled and left open for you to review and submit manually

**Recommendation:** keep this off until you've verified the drafted emails and form-fill behavior are accurate for your use case.

## Supported ATS platforms

Auto-fill currently supports:
- Greenhouse
- Lever
- Workday

Jobs on other platforms (iCIMS, SuccessFactors, Taleo, custom company portals, etc.) are skipped for auto-apply — use the drafted email or apply manually instead, then log the result with **Mark as applied manually**.

## Tech stack

Python · Streamlit · Playwright · Ollama · SQLite · Adzuna API

## Disclaimer

This tool automates form submission on third-party websites. Use responsibly, respect each platform's terms of service, and always review AI-drafted content before it's sent on your behalf.
