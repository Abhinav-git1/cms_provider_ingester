"""
generate_answer.py

Full RAG pipeline: takes a natural-language question, retrieves the
most relevant providers via pgvector similarity search, then passes
them to Groq (via LangChain) to generate a grounded, natural-language answer.
"""
from __future__ import annotations
import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.messages import SystemMessage, HumanMessage

from retrieve import retrieve_similar_providers

load_dotenv()

GROQ_MODEL = "llama-3.1-8b-instant"

llm = ChatGroq(
    model=GROQ_MODEL,
    api_key=os.getenv("GROQ_API_KEY"),
    temperature=0.2,
)


def format_providers_for_prompt(providers: list[dict]) -> str:
    lines = []
    for p in providers:
        name = f"{p.get('first_name') or ''} {p.get('last_name') or ''}".strip() or p.get('organization_name') or "Unknown"
        lines.append(
            f"- {name}, {p.get('taxonomy_desc') or 'Unknown specialty'}, "
            f"located in {p.get('city') or '?'}, {p.get('state') or '?'} {p.get('zip_code') or ''}"
        )
    return "\n".join(lines)


def answer_question(question: str, top_k: int = 5) -> str:
    providers = retrieve_similar_providers(question, top_k=top_k)

    if not providers:
        return "I couldn't find any matching providers in the database for that question."

    context = format_providers_for_prompt(providers)

    system_prompt = (
        "You are a helpful assistant that recommends healthcare providers "
        "based ONLY on the data provided below. Do not invent providers, "
        "credentials, or details not present in the context. If the context "
        "doesn't contain a good match, say so honestly rather than guessing.\n\n"
        f"Available providers:\n{context}"
    )

    response = llm.invoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=question),
    ])

    return response.content


if __name__ == "__main__":
    test_question = "who can help with chest pain near Bethesda"
    print(f"Question: {test_question}\n")
    print("Answer:")
    print(answer_question(test_question))
