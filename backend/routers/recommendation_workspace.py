"""An isolated local sub-application; cloud auth overrides never leak globally."""

from auth_deps import current_user
from fastapi import Depends, FastAPI, HTTPException, Request
from services import recommendation_storage
from services.local_recommendations import get_local_db, local_store, local_user

from routers.recommendations import endpoints
from routers.sync import get_cloud_db


def require_loopback(request: Request):
    if not request.client or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "本地推荐仅允许本机访问；远程使用请通过 SSH 隧道")


def create_workspace_app():
    app = FastAPI(dependencies=[Depends(require_loopback)])
    app.include_router(endpoints, prefix="/personal")

    @app.get("/personal/storage")
    def storage_preview():
        return recommendation_storage.preview(local_store())

    @app.post("/personal/storage/cleanup")
    def storage_cleanup():
        return recommendation_storage.cleanup(local_store())

    app.dependency_overrides[get_cloud_db] = get_local_db
    app.dependency_overrides[current_user] = local_user
    return app
