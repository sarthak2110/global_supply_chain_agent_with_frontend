"""
Supplier Negotiator Agent:
Logic: Orchestrates communication with backup suppliers when the primary source fails.
It automatically requests quotes, compares pricing, and drafts purchase orders that comply with company finance policies.
"""

# agent.py

from datetime import date
from google.adk.agents.llm_agent import Agent

from .prompts import build_supplier_negotiator_prompt
from .tools.load_excel_data_tool import load_data_from_excel, to_json_blobs


def create_agent() -> Agent:
    """
    ADK calls this function dynamically.
    We load Excel files ONLY here (not at import time).
    """
    suppliers_list, quotes_list, finance_policy = load_data_from_excel()

    suppliers_json, quotes_json, finance_policy_json = to_json_blobs(
        suppliers_list, quotes_list, finance_policy
    )

    instruction = build_supplier_negotiator_prompt(
        suppliers_json=suppliers_json,
        quotes_json=quotes_json,
        finance_policy_json=finance_policy_json,
        today_iso=date.today().isoformat(),
    )

    return Agent(
        model="gemini-2.5-flash",
        name="supplier_negotiator_agent",
        description=(
            "Orchestrates backup supplier negotiation, compares quotes, "
            "and drafts POs per finance policy."
        ),
        instruction=instruction,
    )


# Export an object named `root_agent` for ADK Web
supplier_negotiator_agent = create_agent()

if __name__ == "__main__":
    # Manual local test (not used by ADK web)
    agent = create_agent()
    user_task = """
Primary supplier for SKU STEEL-A36 is unable to fulfill.
Need 300 units delivered to Hyderabad within 10 days.
Please evaluate backups using available quotes, recommend the best option, and draft a PO.
"""
    if hasattr(agent, "invoke"):
        print(agent.invoke(user_task))
    else:
        print(agent(user_task))