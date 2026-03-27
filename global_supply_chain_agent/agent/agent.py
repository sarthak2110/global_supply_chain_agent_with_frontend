"""
Central Orchestrator (The "Brain"):
Logic: Manages the "Dynamic Agent Selection" process. It identifies the user's intent, 
delegates tasks to the sub-agents, shares relevant context between them, 
and consolidates the final recommendation for the user.
"""



from google.adk.agents.llm_agent import Agent
from .sub_agents import inventory_analyst_agent, logistics_resolver_agent, supplier_negotiator_agent
from .prompt import central_orchestrator_agent_prompt


root_agent = Agent(
    model='gemini-2.5-flash',
    name='root_agent',
    description='A helpful assistant for user questions.',
    instruction=central_orchestrator_agent_prompt,
    sub_agents=[
        inventory_analyst_agent,
        logistics_resolver_agent,
        supplier_negotiator_agent
        ],
)
