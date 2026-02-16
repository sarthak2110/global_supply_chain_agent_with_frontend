"""
Inventory Analyst Agent:
Logic: Monitors real-time stock levels across various warehouses and predicts potential stockouts based on seasonal demand and lead times.
Tools: Accesses internal inventory databases and demand forecasting models.
"""

from google.adk.agents.llm_agent import Agent
from .tools.bigquery_mcp import bigquery_toolset
from .prompt import inventory_agent_prompt, generate_inventory_prompt
from agents.config import INVENTORY_AGENT_LLM_MODEL_NAME


inventory_analyst_agent = Agent(
    model=INVENTORY_AGENT_LLM_MODEL_NAME,
    name='inventory_analyst_agent',
    description='A helpful assistant for inventory analysis on data warehouse located on bigquery.',
    instruction=generate_inventory_prompt(),
    tools=[bigquery_toolset],
)
