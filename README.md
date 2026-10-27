# AI Business Analyst Agent (Chat with Bank Loan Data)

Ask business questions in plain English and get back the SQL, the result table,
a short business insight, and a chart. Built on top of my
[Bank Loan Analysis](https://github.com/gauriwasekar08/bank-loan-analysis) dataset.

**Stack:** Python, SQLite, Pandas, Matplotlib, Groq API (GPT-OSS 120B)

## How it works

```
question -> LLM writes SQL -> safety check -> run on read-only SQLite
         -> LLM explains result (finding / so what / recommendation) -> chart
```

1. **Text-to-SQL:** the LLM sees the table schema, sample rows, the exact category
   values (e.g. `'Charged Off'`) and my business-rule definitions.
2. **Safety:** only a single `SELECT`/`WITH` statement is allowed, writes are blocked
   by a keyword check, and the database is opened in read-only mode.
3. **Self-repair:** if a query fails, the error is sent back to the LLM (max 2 retries).
4. **Insight:** the LLM may use only the numbers in the result table.
5. **Chart:** a bar chart is saved to `charts/` when the result is label + number.

## Setup

```bash
python -m venv venv
venv\Scripts\activate          # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
```

1. Get a free API key from console.groq.com and put it in a `.env` file
   (see `.env.example`).
2. Put `financial_loan.csv` in this folder and run `python load_data.py`.
3. Run the agent:

```bash
python agent.py                                   # interactive
python agent.py "Which loan purpose has the highest default rate?"
```

## Example questions

- Which loan purpose has the highest default rate?
- Show the default rate by grade.
- What is the average interest rate for good loans vs bad loans?
- Top 5 states by total funded amount.
- How many applications were received each month?

## Tests

```bash
python -m pytest
```

Tests use a fake LLM and a tiny database, so they run without an API key. They
cover SQL safety, read-only enforcement, query repair and the full pipeline.

## Limitations

- The default definition (`loan_status = 'Charged Off'`) is set in `BUSINESS_RULES`
  in `agent.py`; change it if your definition differs.
- The LLM can still write a wrong query that runs without error. Always check the
  SQL shown with each answer.
- The keyword safety check is simple: a question containing a word like "update"
  inside a text value may be rejected.

## What I learned

(Add 2-3 lines in your own words after you build and run it.)
