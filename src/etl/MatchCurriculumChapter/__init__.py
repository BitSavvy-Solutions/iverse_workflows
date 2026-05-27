import logging
import json
import os
from typing import List, TypedDict, Optional

import azure.functions as func
from pymongo import MongoClient
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END

# Import the embedding and math helpers from your existing search service
from shared.curriculum_search import _get_embedding, _cosine_similarity

# ==========================================
# 1. Define the Data Structures
# ==========================================
class ChapterMatch(BaseModel):
    chapterId: Optional[str] = Field(description="The ID of the matched chapter. Null if no match.")
    chapterTitle: Optional[str] = Field(description="The title of the matched chapter. Null if no match.")
    materialId: Optional[str] = Field(description="The ID of the matched material. Null if no match.")
    materialTitle: Optional[str] = Field(description="The title of the matched material. Null if no match.")
    confidence: str = Field(description="Confidence level: 'high', 'medium', or 'low'.")
    reasoning: str = Field(description="Briefly explain why this specific material was chosen over the others.")

class MatchState(TypedDict):
    text_to_match: str
    course_id: str
    top_matches: List[dict]
    final_match: Optional[dict]
    errors: List[str]

# ==========================================
# 2. Define the Nodes
# ==========================================

def vector_search_node(state: MatchState) -> MatchState:
    """Node 1: Get embeddings and find the Top 3 closest materials in Cosmos DB"""
    logging.info("Graph: Running Vector Search for Top 3")
    try:
        # 1. Get embedding for the student's text
        query_embedding = _get_embedding(state["text_to_match"])
        
        # 2. Connect to Cosmos DB
        client = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])
        collection = client["coursedb"]["curriculumEmbeddings"]
        
        # 3. Fetch all materials for this course
        materials = list(collection.find(
            {"courseId": state["course_id"]},
            {"chapterId": 1, "chapterTitle": 1, "materialId": 1, "materialTitle": 1, "embedding": 1}
        ))
        client.close()

        if not materials:
            return {"errors": ["No materials found in DB for this course."]}

        # 4. Calculate similarity scores
        scored = []
        for mat in materials:
            if not mat.get("embedding"): 
                continue
            score = _cosine_similarity(query_embedding, mat["embedding"])
            scored.append((score, mat))

        # 5. Sort highest to lowest
        scored.sort(key=lambda x: x[0], reverse=True)
        
        # 6. Take the Top 3
        top_3 = []
        for score, mat in scored[:3]:
            top_3.append({
                "chapterId": mat["chapterId"],
                "chapterTitle": mat["chapterTitle"],
                "materialId": mat["materialId"],
                "materialTitle": mat["materialTitle"],
                "vector_score": score
            })
            logging.info(f"Top Match: {mat['materialTitle']} (Score: {score:.4f})")
        
        return {"top_matches": top_3}

    except Exception as e:
        logging.error(f"Vector search failed: {e}")
        return {"errors": [f"Vector search error: {str(e)}"]}


def llm_decision_node(state: MatchState) -> MatchState:
    """Node 2: Let the LLM choose the best match from the Top 3"""
    logging.info("Graph: Running LLM Decision")
    
    if not state.get("top_matches"):
        return {"final_match": None}

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        return {"errors": ["OPENROUTER_API_KEY is not set"]}

    llm = ChatOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
        model="google/gemini-3-flash-preview", 
        temperature=0
    )
    
    structured_llm = llm.with_structured_output(ChapterMatch)

    # Format the Top 3 options into a readable string for the prompt
    options_text = ""
    for i, match in enumerate(state["top_matches"]):
        options_text += f"Option {i+1}:\n"
        options_text += f"- Chapter: {match['chapterTitle']} (ID: {match['chapterId']})\n"
        options_text += f"- Material: {match['materialTitle']} (ID: {match['materialId']})\n"
        options_text += f"- Vector Similarity Score: {match['vector_score']:.4f}\n\n"

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are an AI assistant that maps a student's daily update to the correct curriculum material.\n"
                   "You are provided with the top 3 closest matches from our vector database.\n\n"
                   "INSTRUCTIONS:\n"
                   "1. Review the student's update.\n"
                   "2. Review the 3 provided options.\n"
                   "3. Select the option that BEST describes what the student actually worked on.\n"
                   "4. If NONE of the options make sense (e.g., the update is just 'I was sick today' or 'Attended a meeting'), return null for all ID and Title fields.\n"
                   "5. Provide your reasoning and a confidence score.\n\n"
                   "OPTIONS:\n{options}"),
        ("user", "Student Update: {text}")
    ])

    try:
        chain = prompt | structured_llm
        result = chain.invoke({
            "options": options_text,
            "text": state["text_to_match"]
        })
        
        final_dict = result.model_dump()
        
        # If the LLM actually picked something, tag the method
        if final_dict.get("chapterId"):
            final_dict["matchMethod"] = "vector+llm"
            return {"final_match": final_dict}
        else:
            return {"final_match": None}

    except Exception as e:
        logging.error(f"LLM Decision failed: {e}")
        return {"errors": [f"LLM error: {str(e)}"]}

# ==========================================
# 3. Build the Graph
# ==========================================
workflow = StateGraph(MatchState)

workflow.add_node("vector_search", vector_search_node)
workflow.add_node("llm_decision", llm_decision_node)

workflow.set_entry_point("vector_search")
workflow.add_edge("vector_search", "llm_decision")
workflow.add_edge("llm_decision", END)

app = workflow.compile()

# ==========================================
# 4. Azure Function Entry Point
# ==========================================
def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("MatchCurriculumChapter triggered")

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Invalid JSON body"}),
            status_code=400, mimetype="application/json"
        )

    # Accept either a list of extracted updates OR raw text
    updates = body.get("updates", [])
    raw_text = body.get("raw_text", "")
    course_id = body.get("courseId", "fullstack-2025")

    # Combine updates into a single string for matching
    if updates:
        text_to_match = "\n".join(updates)
    else:
        text_to_match = raw_text

    if not text_to_match:
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Provide 'updates' array or 'raw_text'"}),
            status_code=400, mimetype="application/json"
        )

    initial_state = MatchState(
        text_to_match=text_to_match,
        course_id=course_id,
        top_matches=[],
        final_match=None,
        errors=[]
    )

    try:
        result = app.invoke(initial_state)
    except Exception as e:
        logging.error(f"Graph execution failed: {e}")
        return func.HttpResponse(
            json.dumps({"success": False, "error": "Internal workflow error"}),
            status_code=500, mimetype="application/json"
        )

    if result.get("errors"):
        return func.HttpResponse(
            json.dumps({"success": False, "error": result["errors"][0]}),
            status_code=500, mimetype="application/json"
        )

    return func.HttpResponse(
        json.dumps({
            "success": True,
            "data": result.get("final_match")
        }),
        status_code=200,
        mimetype="application/json"
    )