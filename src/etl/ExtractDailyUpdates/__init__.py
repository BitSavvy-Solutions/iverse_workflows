"""
src/etl/ExtractDailyUpdates/__init__.py
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
    dates: List[str] = Field(
        description="A list of specific dates for this update in YYYY-MM-DD format. "
                    "If the student provides different updates for different days (e.g., 'yesterday' vs 'today'), "
                    "you MUST create a SEPARATE object for each day. Only group dates in this array if the exact same update applies to multiple days."
    )
    updates: List[str] = Field(
        description="A list of exact text items describing what the student DID. "
                    "ONLY include completed work (e.g., 'today', 'yesterday'). "
                    "DO NOT include future plans (e.g., 'tomorrow', 'what I will do'). "
                    "EXTRACT THE EXACT ORIGINAL TEXT. DO NOT rephrase, summarize, or fix typos. "
                    "Split multiple lines or distinct statements into separate items."
    )
    blockers: List[str] = Field(
        description="A list of exact text items describing anything blocking the student. "
                    "EXTRACT THE EXACT ORIGINAL TEXT. DO NOT rephrase, summarize, or fix typos. "
                    "If there are no blockers mentioned, leave this array empty."
    )

class ExtractedUpdates(BaseModel):
    extracted_data: List[DailyUpdate] = Field(
        description="List of updates and blockers extracted from the message."
    )

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
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"errors": ["OPENROUTER_API_KEY is not set"]}

    llm = ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        model="google/gemini-3-flash-preview", 
        temperature=0
    )
    
    structured_llm = llm.with_structured_output(ExtractedUpdates)
    
    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an AI assistant that extracts daily progress updates and blockers from student messages.\n"
                   "The message was posted on {message_date}. Use this to resolve relative words like 'today' or 'yesterday'.\n"
                   "CRITICAL INSTRUCTIONS:\n"
                   "1. SEPARATE BY DAY: If the student reports different activities for different days (e.g., 'yesterday' vs 'today'), you MUST create a separate update object for each day.\n"
                   "2. IGNORE FUTURE PLANS: ONLY extract work that has already been done. DO NOT extract what the student plans to do 'tomorrow' or in the future.\n"
                   "3. Extract the completed daily updates into the 'updates' array. Each bullet point, line, or distinct statement should be its own item.\n"
                   "4. Extract any reported blockers into the 'blockers' array. If they say 'None' or 'Nothing', include that exact text.\n"
                   "5. DO NOT ALTER THE TEXT. You must extract the exact original text written by the user. Do not fix typos, do not rephrase, do not summarize."),
        ("user", "{raw_text}")
    ])
    
    chain = prompt | structured_llm
    
    try:
        result = chain.invoke({
            "message_date": state["message_date"],
            "raw_text": state["raw_text"]
        })
        
        updates = [update.model_dump() for update in result.extracted_data]
        return {"extracted_updates": updates, "errors": []}
        
    except Exception as e:
        logging.error(f"LLM Extraction failed: {e}")
        return {"errors": [str(e)]}

# ==========================================
# 4. Build the Graph
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

    initial_state = ExtractionState(
        raw_text=raw_text,
        message_date=message_date,
        extracted_updates=[],
        errors=[]
    )

    try:
        result = app.invoke(initial_state)
    except Exception as e:
        logging.error(f"Graph execution failed: {e}")
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Internal workflow error"}),
            status_code=500,
            mimetype="application/json"
        )

    if result.get("errors"):
        return func.HttpResponse(
            json.dumps({"success": False, "error": result["errors"][0]}),
            status_code=500,
            mimetype="application/json"
        )

    return func.HttpResponse(
        json.dumps({
            "success": True,
            "data": result.get("extracted_updates", [])
        }),
        status_code=200,
        mimetype="application/json"
    )