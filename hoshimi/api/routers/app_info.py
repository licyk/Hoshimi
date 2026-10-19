"""App version, health and what the interface needs to know about this server."""

from fastapi import APIRouter, Request

from hoshimi.api.deps import ServicesDep
from hoshimi.core.net.ports import is_loopback
from hoshimi.core.providers import real_account_count
from hoshimi.core.record import Record
from hoshimi.version import VERSION

router = APIRouter(prefix="/v1/app", tags=["app"])


class AppVersion(Record):
    version: str


class Health(Record):
    status: str
    auth_required: bool


class AppMeta(Record):
    version: str
    auth_required: bool
    model_api_key_configured: bool
    local: bool
    """True when this request comes from the server's own machine."""
    api_prefix: str
    data_dir: str
    config_path: str | None
    default_model: str | None
    providers: int
    accounts: int


@router.get("/version", operation_id="get_app_version")
def get_version() -> AppVersion:
    return AppVersion(version=VERSION)


@router.get("/health", operation_id="get_health")
def get_health(services: ServicesDep) -> Health:
    return Health(status="ok", auth_required=bool(services.settings.access_token))


@router.get("/meta", operation_id="get_app_meta")
def get_meta(request: Request, services: ServicesDep) -> AppMeta:
    client = request.client.host if request.client else ""
    accounts = sum(
        real_account_count(provider) for provider in services.registry.providers.values()
    )
    return AppMeta(
        version=VERSION,
        auth_required=bool(services.settings.access_token),
        model_api_key_configured=bool(services.settings.model_api_key),
        local=bool(client) and (is_loopback(client) or client == "testclient"),
        api_prefix=getattr(request.app.state, "api_prefix", "") or "",
        data_dir=str(services.data_dir),
        config_path=str(services.config_path) if services.config_path else None,
        default_model=services.settings.default_model,
        providers=len(services.registry.providers),
        accounts=accounts,
    )
