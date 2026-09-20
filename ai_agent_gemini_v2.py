"""
AI Agent: Data Quality & Insights Summarizer (Gemini version)
----------------------------------------------------------------
Reads the ETL pipeline's validation report and summary statistics from the
SQLite database, then calls Google's Gemini API (free tier, no credit card
required) to generate a plain-English data quality summary and highlight
the most notable operational insight.

Requires:
    pip install google-genai
    Get a free API key at: https://aistudio.google.com/apikey (just needs a
    Google account, no billing/card needed for the free tier)

    Then set it: set GEMINI_API_KEY=your_key_here

Run (after etl_pipeline.py has already produced the .db and report):
    python ai_agent_gemini.py

Output:
    insights.md
"""

import json
import sqlite3
import os
import sys
from datetime import datetime

DB_FILE = "port_vessel_operations.db"
VALIDATION_REPORT = "validation_report.json"
OUTPUT_FILE = "insights.md"
MODEL = "gemini-3.6-flash"


def load_validation_report(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def compute_summary_stats(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM Operations")
    total_ops = cur.fetchone()[0]

    cur.execute("""
        SELECT p.Port, AVG(o.ActualDelay_Min) as avg_delay
        FROM Operations o JOIN Ports p ON o.PortID = p.PortID
        GROUP BY p.Port ORDER BY avg_delay DESC
    """)
    delay_by_port = cur.fetchall()

    cur.execute("SELECT SUM(CargoVolume_Tons) FROM Operations")
    total_cargo = cur.fetchone()[0]

    cur.execute("SELECT AVG(OnTime) FROM Operations")
    on_time_rate = cur.fetchone()[0]

    conn.close()

    return {
        "total_operations": total_ops,
        "total_cargo_tons": round(total_cargo, 1),
        "on_time_rate": round(on_time_rate, 4),
        "avg_delay_by_port": [{"port": p, "avg_delay_min": round(d, 1)} for p, d in delay_by_port],
    }


def build_prompt(validation: dict, stats: dict) -> str:
    return f"""You are a data quality assistant for a port operations analytics project.
Given the pipeline's validation report and summary statistics below, write a short,
plain-English report for a non-technical stakeholder with two sections:

1. Data Quality — one paragraph, based ONLY on the validation report. State clearly
   whether the data passed checks, and mention any warnings.
2. Key Insight — one paragraph identifying the single most notable pattern in the
   summary statistics (e.g. which port is the biggest delay outlier, and by how much
   relative to the others). Be specific with numbers.

Do not invent any numbers not present in the data below. Keep the total response
under 150 words. Do not use markdown headers, just two short paragraphs.

VALIDATION REPORT:
{json.dumps(validation, indent=2)}

SUMMARY STATISTICS:
{json.dumps(stats, indent=2)}
"""


def call_gemini(prompt: str) -> str:
    try:
        from google import genai
    except ImportError:
        print("Missing dependency. Run: pip install google-genai", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Set your API key first: set GEMINI_API_KEY=your_key_here", file=sys.stderr)
        sys.exit(1)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text


def main():
    print("[AGENT] Loading validation report and computing summary stats ...")
    validation = load_validation_report(VALIDATION_REPORT)
    stats = compute_summary_stats(DB_FILE)

    prompt = build_prompt(validation, stats)
    print("[AGENT] Calling Gemini to generate the summary ...")
    summary = call_gemini(prompt)

    with open(OUTPUT_FILE, "w") as f:
        f.write(f"# Data Quality & Insights Report\n\n")
        f.write(f"_Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} by ai_agent_gemini.py_\n\n")
        f.write(summary.strip() + "\n")

    print(f"[AGENT] Summary written to {OUTPUT_FILE}")
    print("\n--- Preview ---\n")
    print(summary)


if __name__ == "__main__":
    main()
