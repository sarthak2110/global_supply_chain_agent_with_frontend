from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

app = FastAPI()

# --- 1. ENABLE ALL CROSS ORIGIN (CORS) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Mount static files (CSS, JS, Images)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Templates for HTML
templates = Jinja2Templates(directory="templates")

# Data model for chat
class ChatMessage(BaseModel):
    message: str

# --- ROUTES ---

@app.get("/")
async def read_root(request: Request):
    """Serves the main dashboard"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/chat")
async def chat_endpoint(chat: ChatMessage):
    """Simulates the Agent Brain"""
    user_query = chat.message.lower()
    
    # Mock logic - replace with your real agent
    if "delay" in user_query:
        return {"response": "Searching live vessel data... Detected 48h delay at Suez Canal. Rerouting options available."}
    elif "cost" in user_query:
        return {"response": "Analyzing rates... Air Freight is 3x faster but +40% cost vs Ocean for SKU-882."}
    else:
        return {"response": f"SCM-GPT processed: '{chat.message}'. Checking inventory levels..."}

# To run: uvicorn main:app --reload