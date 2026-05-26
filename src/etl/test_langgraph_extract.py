"""
src/etl/test_langgraph_extraction.py

Run this script locally to test the LangGraph extraction logic.
Make sure OPENROUTER_API_KEY is set in your .env file.
"""

import os
import json
from datetime import datetime
from typing import List, TypedDict
from dotenv import load_dotenv

from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

# Load environment variables
load_dotenv()

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
    message_date: str  # The date the message was posted (reference for "today")
    extracted_updates: List[dict]
    errors: List[str]

# ==========================================
# 3. Define the Nodes
# ==========================================
def extract_dates_and_content(state: ExtractionState) -> ExtractionState:
    """Node that uses an LLM to extract structured dates and content."""
    
    # We use OpenAI client pointing to OpenRouter
    # gpt-4o-mini or anthropic/claude-3-haiku are great for structured extraction
    llm = ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        model="openai/gpt-4o-mini", 
        temperature=0
    )
    
    # Bind the Pydantic model to force JSON output
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
        
        # Convert Pydantic objects to dicts for the state
        updates = [update.model_dump() for update in result.updates]
        return {"extracted_updates": updates}
        
    except Exception as e:
        return {"errors": [str(e)]}

# ==========================================
# 4. Build the Graph
# ==========================================
workflow = StateGraph(ExtractionState)
workflow.add_node("extract", extract_dates_and_content)
workflow.set_entry_point("extract")
workflow.add_edge("extract", END)

# Compile the graph
app = workflow.compile()

# ==========================================
# 5. Test Suite (From Slack Conversation)
# ==========================================
TEST_CASES = [
    {
        "name": "Example 1: The Standard Hyphenated Range",
        "date": "2026-04-21",
        "text": """What I did today 19-20 April 2026?
Attended 1:1 session
Busy doing corrections on my bootstrap portfolio
Busy deploying my typescript (Task manager App) on NETLIFY
Continue studying databases- relational queries in SQL"""
    },
    {
        "name": "Example 2: The Diary (Sequential Breakdown)",
        "date": "2026-02-22",
        "text": """What id on 19 February 2026
I completed my Chapter Two activity and submitted it to my mentor for review and feedback.
What i did on 20 February 2026
I attended a Django event at NUST, where I gained additional knowledge...
What i did on 21 February 2026
I completed the final activity for Chapter Two and ensured that all required tasks were properly done."""
    },
    {
        "name": "Example 3: Mixing Relative Days with Hard Dates",
        "date": "2026-05-15",
        "text": """What did I do on Wednesday?13/05
Finished My bootstrap site(Screen recording below)
What did I do on Thursday?14/05
Started with  Lesson 1: SQL basics
Covered Select statemnts, making tables etc
What did  I do today? 15/05
Covered Lesson 2: More advanced SQL queries"""
    },
    {
        "name": "Example 4: Text-Based Ranges",
        "date": "2026-04-30",
        "text": """What I did from the 23- 29 of April 
Had a talk with my team mates on hackathon 
Decided on what to create for the project 
Had a first mentor hour with our mentor for the hackathon"""
    }
]

def run_tests():
    print("🚀 Starting LangGraph Extraction Tests...\n")
    
    for i, test in enumerate(TEST_CASES):
        print(f"{'='*60}")
        print(f"🧪 {test['name']}")
        print(f"📅 Message Date: {test['date']}")
        print(f"{'='*60}")
        
        initial_state = ExtractionState(
            raw_text=test["text"],
            message_date=test["date"],
            extracted_updates=[],
            errors=[]
        )
        
        # Run the graph
        result = app.invoke(initial_state)
        
        if result.get("errors"):
            print(f"❌ Error: {result['errors']}")
        else:
            print(json.dumps(result["extracted_updates"], indent=2))
        print("\n")

if __name__ == "__main__":
    run_tests()