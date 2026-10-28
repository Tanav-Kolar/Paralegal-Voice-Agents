from fastapi import APIRouter

from app.api.routes import access, demo, health, intake_calls, portal_auth, review_api, telephony

api_router = APIRouter()
api_router.include_router(access.router, prefix="/access", tags=["access"])
api_router.include_router(portal_auth.router, prefix="/portal", tags=["portal"])
api_router.include_router(demo.router, prefix="/demo", tags=["demo"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(intake_calls.router, prefix="/intake-calls", tags=["intake-calls"])
api_router.include_router(review_api.router, prefix="/review", tags=["local-review"])
api_router.include_router(telephony.router, prefix="/telephony", tags=["telephony"])
