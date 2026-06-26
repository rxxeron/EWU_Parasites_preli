import os
import hashlib
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
load_dotenv()                                   
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))                       
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))                    
from schemas import AnalyzeTicketRequest, AnalyzeTicketResponse
from investigator import investigate_ticket
from database import cache_manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    await cache_manager.connect()
    yield
    if cache_manager.client:
        await cache_manager.client.aclose()
app = FastAPI(
    title="QueueStorm Investigator API",
    description="High-performance, safe Customer Ticket Classifier and Investigator",
    version="1.0",
    lifespan=lifespan
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
def make_serializable(obj):
    import json
    if isinstance(obj, dict):
        return {k: make_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, set, tuple)):
        return [make_serializable(x) for x in obj]
    elif isinstance(obj, Exception):
        return str(obj)
    else:
        try:
            json.dumps(obj)
            return obj
        except:
            return str(obj)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    """
    Overrides the default FastAPI 422 Unprocessable Entity error.
    Per Section 4.1 of the Problem Statement, malformed inputs must return HTTP 400.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": "Malformed input (invalid JSON, missing required fields).",
            "details": make_serializable(exc.errors())
        }
    )
from starlette.exceptions import HTTPException as StarletteHTTPException
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """
    Global catch-all exception handler to prevent leaking internal stack traces or secrets.
    """
    if isinstance(exc, (HTTPException, StarletteHTTPException)):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail}
        )
    import logging
    logging.getLogger("queuestorm").error(f"Unhandled system error: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "An internal server error occurred during analysis."}
    )
@app.get("/health")
async def health_check():
    """
    Returns the system health.
    Must return exactly {"status": "ok"}.
    """
    return {"status": "ok"}
@app.post("/analyze-ticket", response_model=AnalyzeTicketResponse)
async def analyze_ticket(request: AnalyzeTicketRequest):
    """
    Receives a single CRM ticket and evaluates it against transaction history.
    Uses caching (Redis) for duplicate tickets to maintain low latency.
    """
    if not request.complaint or not request.complaint.strip():
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Semantic error: complaint must not be empty or whitespace only."}
        )
    comp_clean = request.complaint.strip().lower()
    tx_details = []
    for t in (request.transaction_history or []):
        tx_details.append(f"{t.transaction_id}:{t.status}:{t.type}:{t.amount}:{t.counterparty}")
    tx_data = "|".join(tx_details)
    hash_input = f"{comp_clean}:{request.language}:{request.channel}:{request.user_type}:{tx_data}"
    cache_key = f"ticket_cache:{hashlib.md5(hash_input.encode('utf-8')).hexdigest()}"
    cached_resp = await cache_manager.get(cache_key)
    if cached_resp:
        cached_resp["ticket_id"] = request.ticket_id
        return cached_resp
    try:
        api_resp = await investigate_ticket(request)
        if not api_resp.get("human_review_required", False):
            await cache_manager.set(cache_key, api_resp, expire_seconds=3600)
        return api_resp
    except Exception as e:
        import logging
        logging.getLogger("queuestorm").error(f"Internal error during analysis: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred during analysis."
        )
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir) and os.path.isdir(static_dir):
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    print(f"Mounted static frontend files from: {static_dir}")
else:
    print(f"Static directory not found at {static_dir}. Frontend UI serving skipped. Run 'npm install && npm run build' in the frontend directory.")