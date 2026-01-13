"""
Enterprise AI News Aggregator - Azure Function (Flex Consumption)
Daily digest of high-signal headlines for enterprise AI in higher education.

Configuration is loaded from config.json - edit that file to customize:
- Sources (daily and weekly RSS feeds)
- Relevance keywords and weights
- Categories for grouping articles
- Scoring thresholds

ENVIRONMENT VARIABLES (set in Azure Application Settings):
    SMTP_SERVER         - e.g., smtp.gmail.com
    SMTP_PORT           - e.g., 587
    SENDER_EMAIL        - your sending email
    SENDER_PASSWORD     - app password (use Key Vault reference for production)
    RECIPIENT_EMAIL     - where to send the digest
"""

import azure.functions as func
import feedparser
import requests
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
import logging
from pathlib import Path

app = func.FunctionApp()


# =============================================================================
# LOAD CONFIGURATION FROM config.json
# =============================================================================

def load_config() -> dict:
    """Load configuration from config.json file."""
    config_path = Path(__file__).parent / "config.json"
    try:
        with open(config_path) as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load config.json: {e}")
        return {}

CONFIG = load_config()

# Extract config sections with defaults
SETTINGS = CONFIG.get("settings", {})
DAILY_SOURCES = CONFIG.get("daily_sources", {})
WEEKLY_SOURCES = CONFIG.get("weekly_sources", {})
RELEVANCE_KEYWORDS = {k: v for k, v in CONFIG.get("relevance_keywords", {}).items() if k != "_comment"}
CATEGORIES = CONFIG.get("categories", {})


# =============================================================================
# DATA MODEL
# =============================================================================

@dataclass
class Article:
    """Represents a news article with relevance scoring."""
    title: str
    link: str
    source: str
    published: Optional[datetime] = None
    summary: str = ""
    relevance_score: float = 0.0
    matched_keywords: list = field(default_factory=list)
    category: str = "General"


# =============================================================================
# AGGREGATOR FUNCTIONS
# =============================================================================

def fetch_feed(source_name: str, feed_url: str, lookback_hours: int = 24) -> list[Article]:
    """Fetch and parse a single RSS feed."""
    articles = []
    max_per_source = SETTINGS.get("max_articles_per_source", 10)
    
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; EnterpriseAIAggregator/1.0)'}
        response = requests.get(feed_url, headers=headers, timeout=15)
        response.raise_for_status()
        
        feed = feedparser.parse(response.content)
        cutoff_time = datetime.now() - timedelta(hours=lookback_hours)
        
        for entry in feed.entries[:max_per_source]:
            pub_date = None
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                pub_date = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                pub_date = datetime(*entry.updated_parsed[:6])
            
            if pub_date and pub_date < cutoff_time:
                continue
            
            summary = ""
            if hasattr(entry, 'summary'):
                summary = re.sub(r'<[^>]+>', '', entry.summary)[:500]
            elif hasattr(entry, 'description'):
                summary = re.sub(r'<[^>]+>', '', entry.description)[:500]
            
            article = Article(
                title=entry.title,
                link=entry.link,
                source=source_name,
                published=pub_date,
                summary=summary
            )
            articles.append(article)
            
    except Exception as e:
        logging.warning(f"Could not fetch {source_name}: {e}")
    
    return articles


def categorize(keywords: list[str]) -> str:
    """Assign a category based on matched keywords."""
    category_scores = {}
    
    for category, category_keywords in CATEGORIES.items():
        score = sum(1 for kw in keywords if any(ck in kw.lower() for ck in category_keywords))
        category_scores[category] = score
    
    if category_scores:
        best_category = max(category_scores, key=category_scores.get)
        if category_scores[best_category] > 0:
            return best_category
    
    return "General"


def score_relevance(article: Article) -> Article:
    """Score an article's relevance based on keyword matching."""
    text = f"{article.title} {article.summary}".lower()
    
    score = 0
    matched = []
    
    for keyword, weight in RELEVANCE_KEYWORDS.items():
        if keyword.lower() in text:
            score += weight
            matched.append(keyword)
    
    title_lower = article.title.lower()
    for keyword, weight in RELEVANCE_KEYWORDS.items():
        if keyword.lower() in title_lower:
            score += weight * 0.5
    
    article.relevance_score = score
    article.matched_keywords = matched
    article.category = categorize(matched)
    
    return article


def fetch_all_sources(include_weekly: bool = False) -> list[Article]:
    """Fetch all sources and return scored, filtered articles."""
    all_articles = []
    lookback = SETTINGS.get("lookback_hours", 24)
    
    logging.info("Fetching daily sources...")
    for source_name, feed_url in DAILY_SOURCES.items():
        logging.info(f"  → {source_name}")
        articles = fetch_feed(source_name, feed_url, lookback_hours=lookback)
        all_articles.extend(articles)
    
    if include_weekly:
        logging.info("Fetching weekly sources...")
        for source_name, feed_url in WEEKLY_SOURCES.items():
            logging.info(f"  → {source_name}")
            articles = fetch_feed(source_name, feed_url, lookback_hours=168)
            all_articles.extend(articles)
    
    logging.info(f"Scoring {len(all_articles)} articles...")
    scored = [score_relevance(a) for a in all_articles]
    
    # Use env var override if set, otherwise use config
    min_score = int(os.environ.get("MIN_RELEVANCE_SCORE", SETTINGS.get("min_relevance_score", 5)))
    max_articles = SETTINGS.get("max_articles_total", 25)
    
    relevant = [a for a in scored if a.relevance_score >= min_score]
    relevant.sort(key=lambda x: x.relevance_score, reverse=True)
    relevant = relevant[:max_articles]
    
    logging.info(f"Found {len(relevant)} high-signal articles")
    return relevant


def generate_html_digest(articles: list[Article]) -> str:
    """Generate an HTML email digest."""
    by_category = {}
    for article in articles:
        if article.category not in by_category:
            by_category[article.category] = []
        by_category[article.category].append(article)
    
    category_order = [
        "Higher Ed Specific",
        "Strategy & Leadership",
        "Policy & Governance",
        "Enterprise Tech",
        "AI/ML Developments",
        "General"
    ]
    
    html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 700px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        .container {{
            background-color: white;
            border-radius: 8px;
            padding: 30px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #1a365d;
            border-bottom: 3px solid #3182ce;
            padding-bottom: 10px;
            margin-bottom: 5px;
        }}
        .subtitle {{
            color: #666;
            font-size: 14px;
            margin-bottom: 25px;
        }}
        h2 {{
            color: #2c5282;
            font-size: 18px;
            margin-top: 30px;
            margin-bottom: 15px;
            padding: 8px 12px;
            background-color: #ebf8ff;
            border-left: 4px solid #3182ce;
            border-radius: 0 4px 4px 0;
        }}
        .article {{
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid #e2e8f0;
        }}
        .article:last-child {{
            border-bottom: none;
        }}
        .article-title {{
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 5px;
        }}
        .article-title a {{
            color: #2b6cb0;
            text-decoration: none;
        }}
        .article-title a:hover {{
            text-decoration: underline;
        }}
        .article-meta {{
            font-size: 12px;
            color: #718096;
            margin-bottom: 8px;
        }}
        .source {{
            background-color: #e2e8f0;
            padding: 2px 8px;
            border-radius: 12px;
            font-weight: 500;
        }}
        .score {{
            color: #38a169;
            font-weight: 600;
        }}
        .summary {{
            font-size: 14px;
            color: #4a5568;
        }}
        .keywords {{
            font-size: 11px;
            color: #a0aec0;
            margin-top: 5px;
        }}
        .footer {{
            margin-top: 30px;
            padding-top: 20px;
            border-top: 1px solid #e2e8f0;
            font-size: 12px;
            color: #a0aec0;
            text-align: center;
        }}
        .no-articles {{
            color: #718096;
            font-style: italic;
            padding: 20px;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🎯 Enterprise AI Daily Briefing</h1>
        <div class="subtitle">
            Higher Education Focus • {datetime.now().strftime('%A, %B %d, %Y')} • {len(articles)} high-signal items
        </div>
"""
    
    if not articles:
        html += '<div class="no-articles">No high-relevance articles found today. Check back tomorrow!</div>'
    else:
        for category in category_order:
            if category in by_category:
                html += f'<h2>{category}</h2>'
                
                for article in by_category[category]:
                    pub_str = article.published.strftime('%b %d, %I:%M %p') if article.published else 'Recent'
                    summary_text = article.summary[:300] + ('...' if len(article.summary) > 300 else '')
                    keywords_text = ', '.join(article.matched_keywords[:5])
                    
                    html += f"""
        <div class="article">
            <div class="article-title">
                <a href="{article.link}" target="_blank">{article.title}</a>
            </div>
            <div class="article-meta">
                <span class="source">{article.source}</span> • {pub_str} • 
                <span class="score">Relevance: {article.relevance_score:.0f}</span>
            </div>
            <div class="summary">{summary_text}</div>
            <div class="keywords">Tags: {keywords_text}</div>
        </div>
"""
    
    html += """
        <div class="footer">
            Generated by Enterprise AI News Aggregator<br>
            Focused on: AI Strategy • Policy • Higher Education • Enterprise Deployment
        </div>
    </div>
</body>
</html>
"""
    return html


def generate_text_digest(articles: list[Article]) -> str:
    """Generate a plain text digest."""
    lines = [
        "=" * 60,
        "ENTERPRISE AI DAILY BRIEFING",
        f"Higher Education Focus • {datetime.now().strftime('%A, %B %d, %Y')}",
        f"{len(articles)} high-signal items",
        "=" * 60,
        ""
    ]
    
    by_category = {}
    for article in articles:
        if article.category not in by_category:
            by_category[article.category] = []
        by_category[article.category].append(article)
    
    category_order = [
        "Higher Ed Specific",
        "Strategy & Leadership",
        "Policy & Governance",
        "Enterprise Tech",
        "AI/ML Developments",
        "General"
    ]
    
    for category in category_order:
        if category in by_category:
            lines.append(f"\n▸ {category.upper()}")
            lines.append("-" * 40)
            
            for article in by_category[category]:
                pub_str = article.published.strftime('%b %d') if article.published else 'Recent'
                lines.append(f"\n• {article.title}")
                lines.append(f"  [{article.source}] {pub_str} | Score: {article.relevance_score:.0f}")
                lines.append(f"  {article.link}")
    
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


def send_email(html_content: str, text_content: str) -> bool:
    """Send the digest via email using environment variables for credentials."""
    
    smtp_server = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    sender_email = os.environ.get("SENDER_EMAIL")
    sender_password = os.environ.get("SENDER_PASSWORD")
    recipient_email = os.environ.get("RECIPIENT_EMAIL")
    
    if not all([sender_email, sender_password, recipient_email]):
        logging.error("Email not configured. Set SENDER_EMAIL, SENDER_PASSWORD, RECIPIENT_EMAIL in Application Settings.")
        return False
    
    subject = f"🎯 Enterprise AI Briefing - {datetime.now().strftime('%b %d, %Y')}"
    
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient_email
    
    msg.attach(MIMEText(text_content, "plain"))
    msg.attach(MIMEText(html_content, "html"))
    
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(msg)
        logging.info(f"Email sent to {recipient_email}")
        return True
    except Exception as e:
        logging.error(f"Failed to send email: {e}")
        return False


# =============================================================================
# AZURE FUNCTION ENTRY POINT
# =============================================================================

@app.function_name(name="AINewsDigest")
@app.timer_trigger(schedule="0 0 11 * * *", arg_name="mytimer", run_on_startup=False)
def AINewsDigest(mytimer: func.TimerRequest) -> None:
    """Azure Function entry point - triggered by timer at 6 AM ET (11:00 UTC)."""
    
    logging.info('AI News Digest function started.')
    
    if mytimer.past_due:
        logging.info('Timer is past due, running anyway.')
    
    try:
        articles = fetch_all_sources(include_weekly=False)
        html = generate_html_digest(articles)
        text = generate_text_digest(articles)
        
        if send_email(html, text):
            logging.info("Daily digest sent successfully.")
        else:
            logging.error("Failed to send daily digest.")
            
    except Exception as e:
        logging.error(f"Function failed: {e}")
        raise
    
    logging.info('AI News Digest function completed.')
