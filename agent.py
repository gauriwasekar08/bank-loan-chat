"""AI Business Analyst Agent.

Ask a business question in plain English. The agent:
  1. turns it into a SQL query (LLM via Groq),
  2. checks the query is safe (read-only SELECT),
  3. runs it on the SQLite database,
  4. explains the result as finding / so-what / recommendation,
  5. saves a bar chart when the result is chart-friendly.
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

DB_PATH = "loans.db"
TABLE = "loans"
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
MAX_RETRIES = 2      # how many times the LLM may repair a failing query
PREVIEW_ROWS = 20    # rows shown to the LLM when it explains results
CHART_DIR = Path("charts")

# Edit these so they match how YOU defined things in your Power BI project.
BUSINESS_RULES = """\
- A loan is a "default" / "bad loan" when loan_status = 'Charged Off'.
- A "good loan" is one where loan_status is 'Fully Paid' or 'Current'.
- Default rate = number of charged-off loans / total loans (use CAST(... AS REAL)).
- Dates are stored as text in YYYY-MM-DD format.
"""

FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|vacuum|reindex|truncate)\b",
    re.IGNORECASE,
)


# ----------------------------------------------------------------- LLM call
def chat(messages, temperature=0.0):
    """Call Groq. Kept separate so tests can swap it for a fake."""
    from groq import Groq

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    resp = client.chat.completions.create(model=MODEL, messages=messages, temperature=temperature)
    return resp.choices[0].message.content


# --------------------------------------------------------------- SQL safety
def extract_sql(text):
    """Pull the SQL out of an LLM reply (handles ```sql fences)."""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    return (m.group(1) if m else text).strip()


def validate_sql(sql):
    """Allow exactly one read-only SELECT / WITH statement."""
    s = sql.strip().rstrip(";").strip()
    if not s:
        raise ValueError("Empty query.")
    if ";" in s:
        raise ValueError("Only one statement is allowed.")
    if not re.match(r"^(select|with)\b", s, re.IGNORECASE):
        raise ValueError("Only SELECT queries are allowed.")
    if FORBIDDEN.search(s):
        raise ValueError("Query contains a forbidden keyword.")
    return s


def run_query(sql):
    """Run on a READ-ONLY connection (second layer of protection)."""
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as con:
        return pd.read_sql_query(sql, con)


# ------------------------------------------------------------------- schema
def get_schema(db_path=None):
    """Describe the table for the LLM: columns, types, sample rows, category values."""
    with sqlite3.connect(db_path or DB_PATH) as con:
        cols = con.execute(f"PRAGMA table_info({TABLE})").fetchall()
        lines = [f"Table: {TABLE}", "Columns:"]
        for _, name, ctype, *_ in cols:
            line = f"  - {name} ({ctype})"
            if ctype.upper() == "TEXT":
                vals = [r[0] for r in con.execute(
                    f'SELECT DISTINCT "{name}" FROM {TABLE} WHERE "{name}" IS NOT NULL LIMIT 13')]
                if 0 < len(vals) <= 12:
                    line += " values: " + ", ".join(repr(v) for v in vals)
            lines.append(line)
        sample = pd.read_sql_query(f"SELECT * FROM {TABLE} LIMIT 3", con)
    return "\n".join(lines) + "\n\nSample rows:\n" + sample.to_string(index=False)


# -------------------------------------------------------------- the agent
def generate_sql(question, schema, llm, error=None, previous_sql=None):
    system = (
        "You are an expert SQLite analyst. Write ONE SQLite SELECT query that answers "
        "the user's question. Return only the SQL, no explanation.\n\n"
        f"{schema}\n\nBusiness rules:\n{BUSINESS_RULES}\n"
                "Use only the columns listed above. Use exact category spellings shown.\n"
        "Always return the label AND the computed number (for example default_rate as a percentage "
        "rounded to 1 decimal, named default_rate_pct), never only the label. "
        "For highest/top questions return the top 5 rows ordered by that metric."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
    if error:
        messages.append({"role": "assistant", "content": previous_sql})
        messages.append({"role": "user", "content": f"That query failed: {error}\nFix it. Return only the SQL."})
    return extract_sql(llm(messages))


def explain(question, sql, df, llm):
    system = (
        "You are a senior business analyst writing for a manager. Using ONLY the numbers "
        "in the result table, reply in exactly three short parts:\n"
        "Finding: 1-2 sentences with the key numbers.\n"
        "So what: why it matters for the business.\n"
        "Recommendation: one concrete action.\n"
        "Never invent numbers that are not in the table. Do not claim shares, totals, or percentages of the whole unless they appear in the table."
    )
    user = f"Question: {question}\nSQL: {sql}\nResult ({len(df)} rows):\n{df.head(PREVIEW_ROWS).to_string(index=False)}"
    return llm([{"role": "system", "content": system}, {"role": "user", "content": user}])


def make_chart(df, question):
    """Save a bar chart if the result is label + number with 2-30 rows."""
    if df.shape[1] < 2 or not 2 <= len(df) <= 30:
        return None
    label_col, value_col = df.columns[0], df.columns[-1]
    if not pd.api.types.is_numeric_dtype(df[value_col]):
        return None
    CHART_DIR.mkdir(exist_ok=True)
    path = CHART_DIR / (re.sub(r"[^a-z0-9]+", "_", question.lower())[:50].strip("_") + ".png")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.barh(df[label_col].astype(str), df[value_col], color="#1F4E79")
    ax.invert_yaxis()
    ax.set_xlabel(value_col)
    ax.set_title(question[:80])
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def answer(question, llm=chat, schema=None):
    """Full pipeline. Returns dict with sql, data, insight, chart."""
    schema = schema or get_schema()
    sql, error, last = None, None, None
    for _ in range(MAX_RETRIES + 1):
        sql = generate_sql(question, schema, llm, error, last)
        try:
            last = validate_sql(sql)
            df = run_query(last)
            break
        except Exception as e:  # unsafe or failing SQL -> ask the LLM to repair it
            error, last = str(e), sql
    else:
        return {"sql": sql, "error": error}
    return {
        "sql": last,
        "data": df,
        "insight": explain(question, last, df, llm),
        "chart": make_chart(df, question),
    }


def show(res):
    print("\nSQL:\n" + res["sql"])
    if "error" in res:
        print("\nCould not answer:", res["error"])
        return
    print("\nResult:\n" + res["data"].head(PREVIEW_ROWS).to_string(index=False))
    print("\n" + res["insight"])
    if res["chart"]:
        print(f"\nChart saved: {res['chart']}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        show(answer(" ".join(sys.argv[1:])))
    else:
        print("AI Business Analyst Agent. Type a question, or 'exit'.")
        while True:
            q = input("\nAsk> ").strip()
            if q.lower() in {"exit", "quit"}:
                break
            if q:
                show(answer(q))
