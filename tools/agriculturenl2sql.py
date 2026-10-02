"""Natural-language query tool for the agriculture database.

crewai_tools.NL2SQLTool only executes SQL that the agent writes itself, so
agents end up guessing table and column names. This tool takes a plain-English
question instead, generates the SQL from the real schema with an LLM, runs it
read-only, and returns both the SQL and the rows.

Only one BaseTool subclass may live in this module: the custom tool loader
instantiates the first one it finds.
"""

import os
import re
from typing import Any

from crewai import LLM
from crewai.tools import BaseTool
from pydantic import BaseModel, Field, PrivateAttr
from sqlalchemy import create_engine, inspect, text

DB_URI = os.environ.get(
    "AGRICULTURE_DB_URI", "postgresql://nitisha:nitisha@localhost:5432/agriculture"
)
SQL_LLM_MODEL = os.environ.get("NL2SQL_LLM_MODEL", "ollama/qwen2.5:3b")
SQL_LLM_BASE_URL = os.environ.get("NL2SQL_LLM_BASE_URL", "http://localhost:11434")

MAX_ROWS = 200
MAX_ATTEMPTS = 3
# Text columns with at most this many distinct values have them listed in the
# schema prompt, so the LLM uses exact spellings (e.g. crop = 'cotton').
MAX_LISTED_VALUES = 40

# Worked examples for the SQL-writing LLM; small local models follow these far
# more reliably than rules alone.
SQL_EXAMPLES = """Examples:
Question: How did rice yield in Durg change over the last 5 years?
SQL: SELECT year, yield_kg_per_ha FROM crop_yields WHERE crop = 'rice' AND dist_name ILIKE 'durg' AND year > (SELECT MAX(year) FROM crop_yields) - 5 ORDER BY year

Question: Which 3 districts in Chhattisgarh had the highest average maize yield since 2000?
SQL: SELECT dist_name, AVG(yield_kg_per_ha) AS avg_yield FROM crop_yields WHERE crop = 'maize' AND state_name ILIKE 'chhattisgarh' AND year >= 2000 GROUP BY dist_name ORDER BY avg_yield DESC LIMIT 3

Question: What were the rainfall and wheat yield in Durg in the driest years?
SQL: SELECT year, rainfall_mm, yield_kg_per_ha FROM crop_yields WHERE crop = 'wheat' AND dist_name ILIKE 'durg' ORDER BY rainfall_mm ASC LIMIT 5
"""

_SQL_START_RE = re.compile(
    r"^\s*(select|with|insert|update|delete|drop|alter|create|truncate)\b",
    re.IGNORECASE,
)
_CODE_FENCE_RE = re.compile(r"```(?:sql)?\s*(.*?)```", re.IGNORECASE | re.DOTALL)


class AgricultureNL2SQLInput(BaseModel):
    question: str = Field(
        ...,
        description=(
            "A question in plain English describing the data you need, e.g. "
            "'What was the cotton yield in Nanded district for each year from "
            "2014 to 2023?'. Do NOT write SQL."
        ),
    )


class AgricultureNL2SQLTool(BaseTool):
    name: str = "agriculture_nl2sql"
    description: str = (
        "Answers a plain-English question using the agriculture database "
        "(district-level crop area, yield, fertilizer requirements and weather "
        "by year). Pass your question in natural language, never SQL; the tool "
        "writes and runs the SQL itself and returns the SQL it used together "
        "with the result rows."
    )
    args_schema: type[BaseModel] = AgricultureNL2SQLInput

    _engine: Any = PrivateAttr(default=None)
    _schema: str | None = PrivateAttr(default=None)
    _llm: Any = PrivateAttr(default=None)

    def _get_engine(self):
        if self._engine is None:
            self._engine = create_engine(DB_URI)
        return self._engine

    def _get_llm(self):
        if self._llm is None:
            self._llm = LLM(
                model=SQL_LLM_MODEL, base_url=SQL_LLM_BASE_URL, temperature=0
            )
        return self._llm

    def _get_schema(self) -> str:
        """Describe every table: columns, types, low-cardinality values, sample rows."""
        if self._schema is not None:
            return self._schema

        engine = self._get_engine()
        inspector = inspect(engine)
        parts = []
        with engine.connect() as conn:
            for table in inspector.get_table_names():
                lines = [f"Table {table}:"]
                for col in inspector.get_columns(table):
                    line = f"  - {col['name']} ({col['type']})"
                    if "CHAR" in str(col["type"]).upper() or "TEXT" in str(col["type"]).upper():
                        values = conn.execute(
                            text(
                                f'SELECT DISTINCT "{col["name"]}" FROM "{table}" '
                                f"LIMIT {MAX_LISTED_VALUES + 1}"
                            )
                        ).scalars().all()
                        if len(values) <= MAX_LISTED_VALUES:
                            line += f" values: {sorted(v for v in values if v is not None)}"
                    elif "year" in col["name"].lower():
                        lo, hi = conn.execute(
                            text(f'SELECT MIN("{col["name"]}"), MAX("{col["name"]}") FROM "{table}"')
                        ).one()
                        line += f" range: {lo} to {hi}"
                    lines.append(line)
                rows = conn.execute(text(f'SELECT * FROM "{table}" LIMIT 3')).mappings().all()
                lines.append("  Sample rows:")
                lines.extend(f"    {dict(r)}" for r in rows)
                parts.append("\n".join(lines))

        self._schema = "\n\n".join(parts)
        return self._schema

    def _generate_sql(self, question: str, previous_sql: str | None, error: str | None) -> str:
        prompt = (
            "You write a single PostgreSQL SELECT query that answers the question, "
            "using only the tables and columns below.\n\n"
            f"{self._get_schema()}\n\n"
            "Rules:\n"
            "- Return only the SQL query, with no explanation.\n"
            "- Use a single read-only SELECT (or WITH ... SELECT) statement, no semicolons.\n"
            "- Interpret relative periods such as 'the last 10 years' against the year "
            "range shown above, not the current date.\n"
            "- With GROUP BY, every selected column must be in the GROUP BY or inside "
            "an aggregate such as AVG(); to rank groups, select the aggregate and "
            "ORDER BY it.\n"
            "- Match place names case-insensitively with ILIKE, e.g. dist_name ILIKE 'nanded'.\n"
            "- Select the columns needed to answer the question, plus year and place "
            "columns where relevant, and ORDER BY year when the question is about time.\n"
            f"- Return at most {MAX_ROWS} rows; aggregate if more would be needed.\n"
            "- Use the simplest query that answers the question; do not join a table "
            "to itself unless the question compares rows.\n\n"
            f"{SQL_EXAMPLES}\n"
            f"Question: {question}\nSQL:"
        )
        if previous_sql and error:
            prompt += (
                f"\n\nYour previous query failed.\nQuery: {previous_sql}\n"
                f"Error: {error}\nWrite a corrected query."
            )

        response = self._get_llm().call([{"role": "user", "content": prompt}])
        sql = str(response).strip()
        fenced = _CODE_FENCE_RE.search(sql)
        if fenced:
            sql = fenced.group(1)
        return sql.strip().rstrip(";").strip()

    def _execute_read_only(self, sql: str) -> list[dict[str, Any]]:
        if ";" in sql:
            raise ValueError("Only a single statement is allowed.")
        if not re.match(r"^\s*(select|with)\b", sql, re.IGNORECASE):
            raise ValueError("Only SELECT queries are allowed.")
        with self._get_engine().connect() as conn:
            # The database itself rejects any write inside a read-only transaction.
            conn.execute(text("SET TRANSACTION READ ONLY"))
            result = conn.execute(text(sql))
            rows = [dict(r) for r in result.mappings().fetchmany(MAX_ROWS + 1)]
            conn.rollback()
        return rows

    def _run(self, question: str) -> str:
        if _SQL_START_RE.match(question):
            return (
                "This tool takes a plain-English question, not SQL. Describe the data "
                "you need in natural language, for example: 'What was the cotton yield "
                "in Nanded district for each year from 2014 to 2023?'"
            )

        sql, error = None, None
        for _ in range(MAX_ATTEMPTS):
            try:
                sql = self._generate_sql(question, sql, error)
                rows = self._execute_read_only(sql)
            except Exception as exc:
                error = str(exc).splitlines()[0]
                continue

            if not rows:
                return (
                    f"SQL used: {sql}\nResult: no rows matched. The data may not exist "
                    "for this crop, place or period; try rephrasing the question or "
                    "broadening it."
                )
            truncated = len(rows) > MAX_ROWS
            rows = rows[:MAX_ROWS]
            body = "\n".join(str(r) for r in rows)
            note = f"\n(Showing the first {MAX_ROWS} rows only.)" if truncated else ""
            return f"SQL used: {sql}\nRows returned: {len(rows)}\n{body}{note}"

        return (
            f"Could not answer the question after {MAX_ATTEMPTS} attempts. "
            f"Last SQL tried: {sql}\nLast error: {error}\n"
            "Try asking a simpler or more specific question."
        )


if __name__ == "__main__":
    tool = AgricultureNL2SQLTool()
    print(tool.run(question="How has cotton yield in Nanded district changed over the last 10 years?"))
