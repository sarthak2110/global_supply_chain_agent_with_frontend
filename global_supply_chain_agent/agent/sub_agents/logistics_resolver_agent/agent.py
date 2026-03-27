"""
Logistics Resolver Agent:
Logic: Calls external shipping and weather APIs to identify delays (e.g., port congestion). It autonomously reroutes shipments to maintain connectivity and minimize downtime.
Tools: Interacts with third-party logistics (3PL) tracking systems and mapping services.
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
import os
import asyncio
from .prompt import route_planner_prompt
# from tools.land_tools import land_route_map
# from tools.flying_tools import flying_tracks_map
from .tools import land_route_map, flying_tracks_map

logistics_resolver_agent = Agent(
    model="gemini-2.0-flash",
    name="logistics_resolver_agent",
    description=(
        "An agent that generates interactive route maps for land routes using Google Directions, "
        "and visualizes recent observed flight tracks using OpenSky."
    ),
    instruction=route_planner_prompt,
    tools=[land_route_map, flying_tracks_map],
)






# gcloud beta services enable mapstools.googleapis.com --project=saas-poc-env
# gcloud beta services mcp enable mapstools.googleapis.com --project=saas-poc-env