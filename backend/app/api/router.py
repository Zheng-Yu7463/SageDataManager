from fastapi import APIRouter

from app.api.routes import assets, dashboard, health

router = APIRouter()
router.include_router(health.router)
router.include_router(dashboard.router)
router.include_router(assets.router)
