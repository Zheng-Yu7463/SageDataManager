from fastapi import APIRouter

from app.api.routes import agent, archive, assets, auth, dashboard, files, health, settings

router = APIRouter()
router.include_router(health.router)
router.include_router(dashboard.router)
router.include_router(auth.router)
router.include_router(assets.router)
router.include_router(archive.router)
router.include_router(files.router)
router.include_router(settings.router)
router.include_router(agent.router)
