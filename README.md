# 🕷️ Job Scraper v2 — Indian Job Board Scraper

A production-grade Python web scraper that pulls job listings from **Naukri.com** and **LinkedIn India** using BeautifulSoup, stores results in SQLite (no duplicates), and sends a beautiful HTML **email digest** via Gmail.

---

## ✨ Features

| Feature | Details |
|---|---|
| 🌐 Multi-site scraping | Naukri.com + LinkedIn India |
| 🔍 BeautifulSoup parsing | Parses both JSON-LD structured data and raw HTML cards |
| 💾 SQLite storage | Deduplication via MD5 hash — never saves the same job twice |
| 📧 HTML email digest | Beautiful Gmail digest with one command |
| 📄 CSV export | Auto-exports per keyword per day |
| ⏱ Polite delays | Respects site load with sleep between requests |
| 🔄 Mock fallback | Demo mode when sites block bots |

---

## 🚀 Setup

```bash
pip install requests beautifulsoup4
```

---

## 💻 Usage

```bash
# Basic scrape
python scraper.py --keyword "backend developer" --location "Delhi"

# With custom limit
python scraper.py --keyword "dotnet" --location "Bangalore" --limit 20

# With email digest (needs Gmail App Password)
python scraper.py \
  --keyword "python developer" \
  --location "Delhi" \
  --email yourname@gmail.com \
  --from-email yourname@gmail.com \
  --password "xxxx xxxx xxxx xxxx"
```

---

## 📧 Setting Up Gmail Email Digest

1. Go to **Google Account → Security → 2-Step Verification** → enable it
2. Then go to **App Passwords** → generate one for "Mail"
3. Use that 16-char password as `--password`

---

## 🗄️ Database Schema

```sql
CREATE TABLE jobs (
    id          TEXT PRIMARY KEY,   -- MD5 hash of title+company+source
    title       TEXT,
    company     TEXT,
    location    TEXT,
    experience  TEXT,
    salary      TEXT,
    tags        TEXT,
    source      TEXT,               -- "Naukri" or "LinkedIn"
    url         TEXT,
    date_found  TEXT
);
```

---

## 📁 Project Structure

```
job_scraper_v2/
├── scraper.py      # Main script
├── jobs.db         # SQLite database (auto-created)
├── jobs_*.csv      # Daily CSV exports (auto-created)
└── README.md
```

---

## ⏰ Automate with Cron (Daily Digest)

```bash
# Run every morning at 8am
0 8 * * * /usr/bin/python3 /path/to/scraper.py \
  --keyword "backend developer" \
  --location "Delhi" \
  --email you@gmail.com \
  --from-email you@gmail.com \
  --password "your-app-password"
```

---

## 🧠 Tech Stack

- **Python 3.10+**
- `requests` — HTTP client
- `beautifulsoup4` — HTML parsing
- `sqlite3` — built-in, zero-config database
- `smtplib` — built-in email via Gmail SMTP
- `argparse`, `csv`, `hashlib` — stdlib utilities

---

## 🔮 Possible Extensions

- Add **Indeed India** or **Internshala** scrapers
- Store in **PostgreSQL** for multi-user use
- Build a **Flask dashboard** to browse the DB
- Add **Telegram bot** notifications
- Filter by **salary range** or **experience level**
