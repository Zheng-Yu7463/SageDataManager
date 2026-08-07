from fastapi import APIRouter

from app.api.routes import archive, assets, auth, dashboard, files, health

router = APIRouter()
router.include_router(health.router)
router.include_router(dashboard.router)
router.include_router(auth.router)
router.include_router(assets.router)
router.include_router(archive.router)
router.include_router(files.router)
