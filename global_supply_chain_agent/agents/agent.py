"""
Central Orchestrator (The "Brain"):
Logic: Manages the "Dynamic Agent Selection" process. It identifies the user's intent, 
delegates tasks to the sub-agents, shares relevant context between them, 
and consolidates the final recommendation for the user.
"""



from google.adk.agents.llm_agent import Agent
from google.adk.agents.agent import Agent

# from .sub_agents.inventory_analyst_agent import inventory_analyst_agent
# from .sub_agents.logistics_resolver_agent import logistics_resolver_agent
# from .sub_agents.supplier_negotiator_agent import supplier_negotiator_agent
from .sub_agents.inventory_analyst_agent import inventory_analyst_agent
from .sub_agents.logistics_resolver_agent import logistics_resolver_agent
from .sub_agents.supplier_negotiator_agent import supplier_negotiator_agent

root_agent = Agent(
    model='gemini-2.5-flash',
    model='gemini-1.5-flash',
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction='If any question asked on warehousing or on bigquery, inventory use deligate to inventory_analyst_agent or have a general conversation',
    #sub_agents=[
    #    inventory_analyst_agent,
        # logistics_resolver_agent,
        # supplier_negotiator_agent
    #    ],
    instruction='If any question asked on warehousing or on bigquery, inventory use delegate to inventory_analyst_agent or have a general conversation',
    sub_agents=[
        inventory_analyst_agent,
        logistics_resolver_agent,
        supplier_negotiator_agent
        ],
)
