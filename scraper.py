"""
Job Scraper v2 — Multi-Site Indian Job Board Scraper
=====================================================
Scrapes jobs from Naukri.com & LinkedIn India using BeautifulSoup,
deduplicates via SQLite, and sends a daily email digest.

Usage:
    python scraper.py --keyword "backend developer" --location "Delhi"
    python scraper.py --keyword "dotnet" --email you@gmail.com
    python scraper.py --keyword "python" --location "Bangalore" --email you@gmail.com --limit 20
"""

import argparse
import csv
import hashlib
import json
import re
import smtplib
import sqlite3
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from bs4 import BeautifulSoup


# ─── Config ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

DB_FILE = "jobs.db"


# ─── Database ────────────────────────────────────────────────────────────────

def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            company     TEXT,
            location    TEXT,
            experience  TEXT,
            salary      TEXT,
            tags        TEXT,
            source      TEXT,
            url         TEXT,
            date_found  TEXT
        )
    """)
    conn.commit()


def save_jobs(conn: sqlite3.Connection, jobs: list[dict]) -> tuple[int, int]:
    """Insert new jobs, skip duplicates. Returns (inserted, skipped)."""
    inserted, skipped = 0, 0
    for job in jobs:
        try:
            conn.execute(
                """INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    job["id"], job["title"], job["company"], job["location"],
                    job["experience"], job["salary"], job["tags"],
                    job["source"], job["url"], job["date_found"],
                ),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            skipped += 1
    conn.commit()
    return inserted, skipped


def export_csv(conn: sqlite3.Connection, keyword: str) -> str:
    """Exports all stored jobs to CSV, returns filename."""
    filename = f"jobs_{keyword.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"
    rows = conn.execute("SELECT * FROM jobs ORDER BY date_found DESC").fetchall()
    cols = ["id", "title", "company", "location", "experience", "salary", "tags", "source", "url", "date_found"]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        writer.writerows(rows)
    return filename


def make_id(title: str, company: str, source: str) -> str:
    """Stable unique ID from job title + company + source."""
    raw = f"{title.lower().strip()}{company.lower().strip()}{source}"
    return hashlib.md5(raw.encode()).hexdigest()


# ─── Naukri Scraper ──────────────────────────────────────────────────────────

def scrape_naukri(keyword: str, location: str, limit: int) -> list[dict]:
    """
    Scrapes Naukri.com search results using BeautifulSoup.
    Naukri embeds job data in a <script> JSON blob — we parse that directly.
    """
    slug_kw  = keyword.replace(" ", "-").lower()
    slug_loc = location.replace(" ", "-").lower() if location else ""
    url = (
        f"https://www.naukri.com/{slug_kw}-jobs-in-{slug_loc}"
        if slug_loc else
        f"https://www.naukri.com/{slug_kw}-jobs"
    )

    print(f"  🌐 Naukri → {url}")
    jobs = []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Naukri embeds structured data in <script type="application/ld+json">
        scripts = soup.find_all("script", {"type": "application/ld+json"})
        for script in scripts:
            try:
                data = json.loads(script.string or "")
                # Handle both single job and ItemList
                items = []
                if isinstance(data, list):
                    items = data
                elif data.get("@type") == "ItemList":
                    items = [e.get("item", e) for e in data.get("itemListElement", [])]
                elif data.get("@type") == "JobPosting":
                    items = [data]

                for item in items[:limit]:
                    if item.get("@type") != "JobPosting":
                        continue
                    title   = item.get("title", "N/A")
                    company = item.get("hiringOrganization", {}).get("name", "N/A")
                    loc     = item.get("jobLocation", {})
                    if isinstance(loc, list): loc = loc[0] if loc else {}
                    city    = loc.get("address", {}).get("addressLocality", location or "India")
                    salary  = _parse_salary(item.get("baseSalary", {}))
                    exp     = item.get("experienceRequirements", "Not specified")
                    job_url = item.get("url", url)

                    jobs.append({
                        "id":         make_id(title, company, "naukri"),
                        "title":      title,
                        "company":    company,
                        "location":   city,
                        "experience": str(exp)[:60],
                        "salary":     salary,
                        "tags":       keyword,
                        "source":     "Naukri",
                        "url":        job_url,
                        "date_found": datetime.now().strftime("%Y-%m-%d"),
                    })
            except (json.JSONDecodeError, AttributeError):
                continue

        # Fallback: parse job cards from HTML if JSON-LD was empty
        if not jobs:
            cards = soup.select("article.jobTuple, div.jobTupleHeader, div[class*='srp-jobtuple']")
            for card in cards[:limit]:
                title_el   = card.select_one("a.title, a[class*='jobTitle'], .row1 a")
                company_el = card.select_one("a.subTitle, a[class*='companyInfo'], .row2 a")
                loc_el     = card.select_one("li.location, span[class*='location']")
                exp_el     = card.select_one("li.experience, span[class*='exp']")
                sal_el     = card.select_one("li.salary, span[class*='salary']")
                link_el    = card.select_one("a[href*='naukri.com']") or title_el

                if not title_el:
                    continue

                title   = title_el.get_text(strip=True)
                company = company_el.get_text(strip=True) if company_el else "N/A"
                jobs.append({
                    "id":         make_id(title, company, "naukri"),
                    "title":      title,
                    "company":    company,
                    "location":   loc_el.get_text(strip=True) if loc_el else location or "India",
                    "experience": exp_el.get_text(strip=True) if exp_el else "N/A",
                    "salary":     sal_el.get_text(strip=True) if sal_el else "Not listed",
                    "tags":       keyword,
                    "source":     "Naukri",
                    "url":        link_el.get("href", url) if link_el else url,
                    "date_found": datetime.now().strftime("%Y-%m-%d"),
                })

    except requests.RequestException as e:
        print(f"    ⚠️  Naukri fetch failed: {e}")

    return jobs[:limit]


def _parse_salary(sal: dict) -> str:
    if not sal or not isinstance(sal, dict):
        return "Not listed"
    try:
        val   = sal.get("value", {})
        mn    = val.get("minValue", "")
        mx    = val.get("maxValue", "")
        cur   = sal.get("currency", "INR")
        unit  = sal.get("unitText", "")
        if mn and mx:
            return f"{cur} {mn}–{mx} {unit}".strip()
        elif mn:
            return f"{cur} {mn}+ {unit}".strip()
    except Exception:
        pass
    return "Not listed"


# ─── LinkedIn Scraper ────────────────────────────────────────────────────────

def scrape_linkedin(keyword: str, location: str, limit: int) -> list[dict]:
    """
    Scrapes LinkedIn public job listings (no login required).
    Uses the public jobs search endpoint.
    """
    loc_param = location or "India"
    url = (
        f"https://www.linkedin.com/jobs/search/"
        f"?keywords={requests.utils.quote(keyword)}"
        f"&location={requests.utils.quote(loc_param)}"
        f"&f_TPR=r86400"   # posted in last 24 hours
        f"&position=1&pageNum=0"
    )

    print(f"  🌐 LinkedIn → {url}")
    jobs = []

    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        cards = soup.select("div.job-search-card, li.jobs-search__results-list > div")
        for card in cards[:limit]:
            title_el   = card.select_one("h3.base-search-card__title, h3")
            company_el = card.select_one("h4.base-search-card__subtitle, a.hidden-nested-link")
            loc_el     = card.select_one("span.job-search-card__location")
            date_el    = card.select_one("time")
            link_el    = card.select_one("a.base-card__full-link, a[href*='linkedin.com/jobs']")

            if not title_el:
                continue

            title   = title_el.get_text(strip=True)
            company = company_el.get_text(strip=True) if company_el else "N/A"

            jobs.append({
                "id":         make_id(title, company, "linkedin"),
                "title":      title,
                "company":    company,
                "location":   loc_el.get_text(strip=True) if loc_el else loc_param,
                "experience": "N/A",
                "salary":     "Not listed",
                "tags":       keyword,
                "source":     "LinkedIn",
                "url":        link_el.get("href", url).split("?")[0] if link_el else url,
                "date_found": datetime.now().strftime("%Y-%m-%d"),
            })

    except requests.RequestException as e:
        print(f"    ⚠️  LinkedIn fetch failed: {e}")

    return jobs[:limit]


# ─── Mock Data Fallback ───────────────────────────────────────────────────────

def _mock_jobs(keyword: str, location: str) -> list[dict]:
    loc = location or "Delhi"
    today = datetime.now().strftime("%Y-%m-%d")
    mock = [
        ("Backend .NET Developer",    "Infosys",        loc,         "2-4 yrs", "₹8-12 LPA",  "Naukri"),
        ("C# Software Engineer",      "TCS",            "Hyderabad", "3-5 yrs", "₹10-15 LPA", "Naukri"),
        ("ASP.NET Core Developer",    "Wipro",          "Bangalore", "1-3 yrs", "₹6-9 LPA",   "Naukri"),
        ("Python Backend Engineer",   "PhonePe",        loc,         "2-5 yrs", "₹15-25 LPA", "LinkedIn"),
        ("Software Engineer II",      "Microsoft India","Hyderabad", "3-6 yrs", "₹20-35 LPA", "LinkedIn"),
        ("Full Stack Developer",      "Zomato",         loc,         "2-4 yrs", "₹12-18 LPA", "LinkedIn"),
        ("API Developer .NET",        "HCL Tech",       "Chennai",   "1-3 yrs", "₹5-8 LPA",   "Naukri"),
        ("Associate Software Dev",    "Accenture",      loc,         "0-2 yrs", "₹4-7 LPA",   "Naukri"),
        ("Backend Engineer",          "Razorpay",       "Bangalore", "2-5 yrs", "₹18-28 LPA", "LinkedIn"),
        ("SDE-1 Backend",             "Flipkart",       "Bangalore", "0-2 yrs", "₹12-20 LPA", "LinkedIn"),
    ]
    return [
        {
            "id":         make_id(t, c, s.lower()),
            "title":      t, "company": c, "location": l,
            "experience": e, "salary":  sal, "tags": keyword,
            "source":     s, "url": f"https://{'naukri.com' if s=='Naukri' else 'linkedin.com/jobs'}/view/{i+1}",
            "date_found": today,
        }
        for i, (t, c, l, e, sal, s) in enumerate(mock)
    ]


# ─── Email Digest ────────────────────────────────────────────────────────────

def send_email_digest(jobs: list[dict], keyword: str, to_email: str,
                      from_email: str, app_password: str) -> bool:
    """Sends an HTML email digest of scraped jobs via Gmail SMTP."""
    if not jobs:
        print("  ⚠️  No jobs to email.")
        return False

    subject = f"🔍 Job Digest: {len(jobs)} '{keyword}' roles — {datetime.now().strftime('%d %b %Y')}"

    # Build HTML rows
    rows_html = ""
    for job in jobs:
        source_color = "#0a66c2" if job["source"] == "LinkedIn" else "#ff7555"
        rows_html += f"""
        <tr>
          <td style="padding:10px;border-bottom:1px solid #eee;">
            <a href="{job['url']}" style="color:#1a1a2e;font-weight:600;text-decoration:none;">
              {job['title']}
            </a>
          </td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{job['company']}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{job['location']}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{job['experience']}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">{job['salary']}</td>
          <td style="padding:10px;border-bottom:1px solid #eee;">
            <span style="background:{source_color};color:#fff;padding:2px 8px;
                         border-radius:10px;font-size:12px;">{job['source']}</span>
          </td>
        </tr>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:20px;">
    <div style="max-width:900px;margin:auto;background:#fff;border-radius:10px;
                box-shadow:0 2px 10px rgba(0,0,0,0.1);overflow:hidden;">

      <div style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:30px;color:#fff;">
        <h1 style="margin:0;font-size:24px;">🕷️ Job Digest</h1>
        <p style="margin:8px 0 0;opacity:0.8;">
          {len(jobs)} new <strong>'{keyword}'</strong> roles found •
          {datetime.now().strftime('%d %B %Y')}
        </p>
      </div>

      <div style="padding:20px;">
        <table width="100%" cellspacing="0" cellpadding="0"
               style="border-collapse:collapse;font-size:14px;">
          <thead>
            <tr style="background:#f8f9fa;">
              <th style="padding:12px;text-align:left;color:#555;">Role</th>
              <th style="padding:12px;text-align:left;color:#555;">Company</th>
              <th style="padding:12px;text-align:left;color:#555;">Location</th>
              <th style="padding:12px;text-align:left;color:#555;">Experience</th>
              <th style="padding:12px;text-align:left;color:#555;">Salary</th>
              <th style="padding:12px;text-align:left;color:#555;">Source</th>
            </tr>
          </thead>
          <tbody>{rows_html}</tbody>
        </table>
      </div>

      <div style="background:#f8f9fa;padding:15px;text-align:center;
                  font-size:12px;color:#999;">
        Sent by Job Scraper v2 • Python + BeautifulSoup
      </div>
    </div>
    </body></html>
    """

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = from_email
        msg["To"]      = to_email
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(from_email, app_password)
            server.sendmail(from_email, to_email, msg.as_string())

        print(f"  📧 Email sent → {to_email}")
        return True

    except smtplib.SMTPAuthenticationError:
        print("  ❌ Email failed: wrong credentials. Use a Gmail App Password.")
    except Exception as e:
        print(f"  ❌ Email failed: {e}")
    return False


# ─── Display ─────────────────────────────────────────────────────────────────

def display(jobs: list[dict]) -> None:
    if not jobs:
        print("\n  No results found.")
        return
    print(f"\n{'─'*80}")
    print(f"  {'TITLE':<30} {'COMPANY':<18} {'LOCATION':<14} {'SALARY':<14} SRC")
    print(f"{'─'*80}")
    for j in jobs:
        print(
            f"  {j['title'][:28]:<30} {j['company'][:16]:<18} "
            f"{j['location'][:12]:<14} {j['salary'][:12]:<14} {j['source']}"
        )
    print(f"{'─'*80}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape Indian job boards and get email digests.")
    parser.add_argument("--keyword",   default="backend developer", help="Job keyword")
    parser.add_argument("--location",  default="Delhi",             help="Job location (city)")
    parser.add_argument("--limit",     type=int, default=10,        help="Max results per site")
    parser.add_argument("--email",     default="",                  help="Send digest to this email")
    parser.add_argument("--from-email",default="",                  help="Your Gmail address")
    parser.add_argument("--password",  default="",                  help="Gmail App Password")
    parser.add_argument("--no-mock",   action="store_true",         help="Disable mock fallback")
    args = parser.parse_args()

    print("=" * 80)
    print("         🕷️  Job Scraper v2 — Naukri + LinkedIn India")
    print("=" * 80)
    print(f"  Keyword : {args.keyword}")
    print(f"  Location: {args.location}")
    print(f"  Limit   : {args.limit} per site")

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    all_jobs = []
    start = time.time()

    # Scrape Naukri
    print("\n📌 Scraping Naukri.com...")
    naukri_jobs = scrape_naukri(args.keyword, args.location, args.limit)
    print(f"    → {len(naukri_jobs)} jobs parsed")
    all_jobs.extend(naukri_jobs)
    time.sleep(1.5)  # polite delay

    # Scrape LinkedIn
    print("\n📌 Scraping LinkedIn India...")
    linkedin_jobs = scrape_linkedin(args.keyword, args.location, args.limit)
    print(f"    → {len(linkedin_jobs)} jobs parsed")
    all_jobs.extend(linkedin_jobs)

    # Fallback to mock data if both scrapers returned nothing
    if not all_jobs and not args.no_mock:
        print("\n  ⚠️  Live scraping returned 0 results (sites may block bots).")
        print("  ✅ Using realistic mock data for demo purposes.\n")
        all_jobs = _mock_jobs(args.keyword, args.location)

    # Save to DB
    inserted, skipped = save_jobs(conn, all_jobs)
    print(f"\n💾 Database: {inserted} new jobs saved, {skipped} duplicates skipped")

    # Display
    display(all_jobs)

    # Export CSV
    csv_file = export_csv(conn, args.keyword)
    print(f"📄 CSV exported → {csv_file}")

    # Email digest
    if args.email:
        print(f"\n📧 Sending email digest to {args.email}...")
        send_email_digest(
            jobs=all_jobs,
            keyword=args.keyword,
            to_email=args.email,
            from_email=args.from_email or args.email,
            app_password=args.password,
        )

    elapsed = time.time() - start
    print(f"\n✅ Done in {elapsed:.2f}s\n")
    conn.close()


if __name__ == "__main__":
    main()
