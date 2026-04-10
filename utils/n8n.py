import logging
import httpx
from config import N8N_BASE_URL, N8N_API_KEY

logger = logging.getLogger(__name__)

_HEADERS = {"X-N8N-API-KEY": N8N_API_KEY, "Content-Type": "application/json"}


async def health_check() -> bool:
    """Return True if n8n is reachable, False otherwise."""
    if not N8N_BASE_URL:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{N8N_BASE_URL}/healthz")
            return r.status_code == 200
    except Exception as e:
        logger.warning("n8n health check failed: %s", e)
        return False


async def deploy(workflow_json: dict) -> dict:
    """Create a workflow in n8n. Returns {"id": str, "name": str}. Raises on error."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"{N8N_BASE_URL}/api/v1/workflows",
            json=workflow_json,
            headers=_HEADERS,
        )
        r.raise_for_status()
        data = r.json()
        return {"id": str(data["id"]), "name": data.get("name", "")}


async def activate(workflow_id: str) -> bool:
    """Activate a workflow by ID. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.patch(
                f"{N8N_BASE_URL}/api/v1/workflows/{workflow_id}",
                json={"active": True},
                headers=_HEADERS,
            )
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error("n8n activate failed for %s: %s", workflow_id, e)
        return False


async def deactivate(workflow_id: str) -> bool:
    """Deactivate a workflow by ID. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.patch(
                f"{N8N_BASE_URL}/api/v1/workflows/{workflow_id}",
                json={"active": False},
                headers=_HEADERS,
            )
            r.raise_for_status()
            return True
    except Exception as e:
        logger.error("n8n deactivate failed for %s: %s", workflow_id, e)
        return False


async def list_workflows() -> list[dict]:
    """Return list of all workflows from n8n. Returns [] on error."""
    if not N8N_BASE_URL:
        return []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{N8N_BASE_URL}/api/v1/workflows",
                headers=_HEADERS,
            )
            r.raise_for_status()
            data = r.json()
            return data.get("data", data) if isinstance(data, dict) else data
    except Exception as e:
        logger.error("n8n list_workflows failed: %s", e)
        return []
