import streamlit as st
from langchain_community.utilities import SQLDatabase
from langchain_community.llms import Ollama
from langchain.chains import create_sql_query_chain
from langchain.schema.runnable import RunnablePassthrough, RunnableLambda
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
import re

# --- Database connection ---
db = SQLDatabase.from_uri("postgresql://postgres:postgres@localhost:5432/dvdrental")

# --- Schema info ---
schema_descr = db.get_table_info()

# --- Focused schema prompt ---
focused_schema_prompt = PromptTemplate.from_template(
    """
    You are a senior data engineer helping an LLM write SQL.

    INPUTS
    • QUESTION: {question}
    • RAW_SCHEMA: {schema}

    TASK
    1. Read the QUESTION first. List columns required by QUESTION. Decide which tables are **absolutely required**.
       • Pick the neccessary tables needed to answer the QUESTION.
       • Do NOT include tables whose columns will not appear in SELECT, WHERE, or JOIN.
    2. For each required table, output:
       - One-sentence purpose.
       - Bullet list of relevant columns:
         PK: primary key,FK: foreign key (mention referenced table), human-readable/business/description fields
    3. Then add:
       Necessary columns for query: <comma-separated list>
       Key relationships: <one short sentence per join path>

    STYLE
    • Use markdown bullets.
    • Keep the whole description under 500 words.
    """
)

llm = Ollama(model="llama3.1", temperature=0)

focused_schema_chain = (
    {"question": RunnablePassthrough(), "schema": RunnablePassthrough()}
    | focused_schema_prompt
    | llm
)

write_query_chain = create_sql_query_chain(llm=llm, db=db)

def build_sql(inputs):
    mini_desc = focused_schema_chain.invoke(
        {"question": inputs["question"], "schema": schema_descr}
    )
    prompt = mini_desc + "\n\n### Question: " + inputs["question"]
    sql_text = write_query_chain.invoke({"question": prompt})
    return {"question": inputs["question"], "sql_response": sql_text}

sql_builder = RunnableLambda(build_sql)

def exec_sql(inputs):
    raw = inputs["sql_response"]
    # Priority 1 ─ fenced block ```sql … ```
    block = re.search(r"```sql\s*(.*?)\s*```", raw, re.I | re.S)
    if block:
        sql = block.group(1).strip()
    else:
        # Priority 2 ─ text after 'SQLQuery:' (may span lines)
        after = re.search(r"SQLQuery:\s*(.*)", raw, re.S)
        if after:
            sql = after.group(1).strip()
        else:
            #  last-chance fallback – grab the first SELECT/WITH until the first semicolon
            bare = re.search(r"(?i)\b(SELECT|WITH).*?;", raw, re.S)
            sql = bare.group(0).strip() if bare else ""
    sql = sql.replace("```", "").strip()
    try:
        rows = db.run(sql) if sql else []
    except Exception as e:
        rows = f"Execution error: {e}"
    return {
        "question": inputs["question"],
        "query": sql,
        "result": rows
    }

sql_executor = RunnableLambda(exec_sql)

answer_prompt = PromptTemplate.from_template(
    """You are a helpful data analyst that accurately interprets the result without adding additional information.

Return your reply in **two parts**:

1. `Answer:` – a plain-English explanation of the result.
2. `SQL Query:` – show the exact query inside a ```sql fenced block.

Use the inputs below.

Question: {question}
SQL Query (raw string): {query}
SQL Result: {result}

Respond now."""
)

full_chain = (
    sql_builder
    | sql_executor
    | answer_prompt
    | llm
    | StrOutputParser()
)

# --- Streamlit UI ---
st.title("DVD Rental Llama3.1 SQL Assistant")

# ER diagram image before the question input
st.image("images/er_diagram.jpg", caption="DVD Rental Database ER Diagram", use_container_width =True)

user_q = st.text_input("Ask a question about the DVD rental database:", "Which customer had the highest rentals in 2007?",
                        placeholder="What are the three film categories that brought in the highest total rental revenue across all stores in 2007?")

if st.button("Ask"):
    with st.spinner("Thinking..."):
        answer = full_chain.invoke({"question": user_q})
        st.markdown(answer)