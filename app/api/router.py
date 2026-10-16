from fastapi import APIRouter

from app.api.routes import health, intake_calls, review_ui, telephony

api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(intake_calls.router, prefix="/intake-calls", tags=["intake-calls"])
api_router.include_router(review_ui.router, prefix="/review", tags=["local-review"])
api_router.include_router(telephony.router, prefix="/telephony", tags=["telephony"])
