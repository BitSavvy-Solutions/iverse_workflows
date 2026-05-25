"""
src/etl/ExtractDailyUpdates/__init__.py
────────────────────────────────────────
HTTP endpoint that uses LangGraph to extract structured dates and 
content from raw Slack standup messages.

Endpoint: POST /api/ExtractDailyUpdates
Body: {
    "raw_text": "What I did today 19-20 April...",
    "message_date": "2026-04-21"
}
"""

import logging
import json
import os
from typing import List, TypedDict

import azure.functions as func
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

# ==========================================
# 1. Define the Data Structures (Pydantic)
# ==========================================
class DailyUpdate(BaseModel):
    date: str = Field(description="The specific date for the update in YYYY-MM-DD format. If a range is given, use YYYY-MM-DD to YYYY-MM-DD.")
    content: str = Field(description="The specific tasks, learnings, or updates completed on this date. Exclude noise like 'Blockers' or 'Tomorrow'.")

class ExtractedUpdates(BaseModel):
    updates: List[DailyUpdate] = Field(description="List of updates extracted from the message.")

# ==========================================
# 2. Define the LangGraph State
# ==========================================
class ExtractionState(TypedDict):
    raw_text: str
    message_date: str
    extracted_updates: List[dict]
    errors: List[str]

# ==========================================
# 3. Define the Node
# ==========================================
def extract_dates_and_content(state: ExtractionState) -> ExtractionState:
    """Node that uses an LLM to extract structured dates and content."""
    
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"errors": ["OPENROUTER_API_KEY is not set"]}

    llm = ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        model="openai/gpt-4o-mini", # Fast, cheap, and great at structured JSON
        temperature=0
    )
    
    structured_llm = llm.with_structured_output(ExtractedUpdates)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an AI assistant that extracts daily progress updates from student messages.\n"
                   "The message was posted on {message_date}. Use this to resolve relative words like 'today' or 'yesterday'.\n"
                   "A student might report for a single day, a range of days, or list multiple days.\n"
                   "Extract each distinct time period and its corresponding content.\n"
                   "Clean up the content to only include what they actually did."),
        ("user", "{raw_text}")
    ])
    
    chain = prompt | structured_llm
    
    try:
        result = chain.invoke({
            "message_date": state["message_date"],
            "raw_text": state["raw_text"]
        })
        
        updates = [update.model_dump() for update in result.updates]
        return {"extracted_updates": updates, "errors": []}
        
    except Exception as e:
        logging.error(f"LLM Extraction failed: {e}")
        return {"errors": [str(e)]}

# ==========================================
# 4. Build the Graph (Done globally)
# ==========================================
workflow = StateGraph(ExtractionState)
workflow.add_node("extract", extract_dates_and_content)
workflow.set_entry_point("extract")
workflow.add_edge("extract", END)
app = workflow.compile()

# ==========================================
# 5. Azure Function Entry Point
# ==========================================
def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("ExtractDailyUpdates triggered")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json"
        )

    raw_text = body.get("raw_text")
    message_date = body.get("message_date")

    if not raw_text or not message_date:
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Missing required fields: raw_text, message_date"}),
            status_code=400,
            mimetype="application/json"
        )

    # Initialize state
    initial_state = ExtractionState(
        raw_text=raw_text,
        message_date=message_date,
        extracted_updates=[],
        errors=[]
    )

    # Run the LangGraph workflow
    try:
        result = app.invoke(initial_state)
    except Exception as e:
        logging.error(f"Graph execution failed: {e}")
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Internal workflow error"}),
            status_code=500,
            mimetype="application/json"
        )

    # Handle errors from the node
    if result.get("errors"):
        return func.HttpResponse(
            json.dumps({"success": False, "error": result["errors"][0]}),
            status_code=500,
            mimetype="application/json"
        )

    # Return successful extraction
    return func.HttpResponse(
        json.dumps({
            "success": True,
            "updates": result.get("extracted_updates", [])
        }),
        status_code=200,
        mimetype="application/json"
    )