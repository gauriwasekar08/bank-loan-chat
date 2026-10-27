import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent


@pytest.fixture()
def db(tmp_path, monkeypatch):
    path = tmp_path / "loans.db"
    df = pd.DataFrame({
        "purpose": ["car", "car", "small business", "small business", "debt consolidation"],
        "loan_status": ["Fully Paid", "Charged Off", "Charged Off", "Charged Off", "Fully Paid"],
        "loan_amount": [1000, 2000, 3000, 4000, 5000],
    })
    with sqlite3.connect(path) as con:
        df.to_sql("loans", con, index=False)
    monkeypatch.setattr(agent, "DB_PATH", str(path))
    monkeypatch.chdir(tmp_path)
    return path


@pytest.mark.parametrize("sql", [
    "DROP TABLE loans", "DELETE FROM loans", "SELECT 1; DROP TABLE loans",
    "UPDATE loans SET loan_amount = 0", "PRAGMA table_info(loans)", "",
])
def test_unsafe_sql_is_rejected(sql):
    with pytest.raises(ValueError):
        agent.validate_sql(sql)


def test_select_and_with_allowed():
    assert agent.validate_sql("SELECT * FROM loans;") == "SELECT * FROM loans"
    assert agent.validate_sql("WITH a AS (SELECT 1) SELECT * FROM a")


def test_extract_sql_from_fence():
    assert agent.extract_sql("Here:\n```sql\nSELECT 1\n```") == "SELECT 1"


def test_read_only_connection_blocks_writes(db):
    with pytest.raises(Exception):
        agent.run_query("DELETE FROM loans")


def test_full_pipeline_with_fake_llm(db):
    def fake_llm(messages):
        if "SQLite analyst" in messages[0]["content"]:
            return ("```sql\nSELECT purpose, ROUND(100.0*SUM(loan_status='Charged Off')/COUNT(*),1) "
                    "AS default_rate FROM loans GROUP BY purpose ORDER BY default_rate DESC\n```")
        return "Finding: small business is riskiest."
    res = agent.answer("Which purpose has the highest default rate?", llm=fake_llm)
    assert res["data"].iloc[0]["purpose"] == "small business"
    assert res["data"].iloc[0]["default_rate"] == 100.0
    assert res["chart"] and res["chart"].exists()


def test_agent_repairs_a_bad_query(db):
    calls = {"n": 0}

    def fake_llm(messages):
        if "SQLite analyst" in messages[0]["content"]:
            calls["n"] += 1
            return "SELECT nonexistent_col FROM loans" if calls["n"] == 1 else "SELECT COUNT(*) AS n FROM loans"
        return "ok"
    res = agent.answer("How many loans?", llm=fake_llm)
    assert calls["n"] == 2 and int(res["data"].iloc[0]["n"]) == 5


def test_schema_lists_columns_and_category_values(db):
    s = agent.get_schema()
    assert "purpose" in s and "'Charged Off'" in s
