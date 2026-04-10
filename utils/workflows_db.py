import json
import logging
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent.parent / "data"
_WF_FILE = _DATA_DIR / "user_workflows.json"


class WorkflowRecord(TypedDict):
    workflow_id: str
    name: str
    template_id: str
    created_at: str
    active: bool
    n8n_url: str


def _load() -> dict:
    if not _WF_FILE.exists():
        return {}
    try:
        return json.loads(_WF_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _WF_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_WF_FILE)


def add_workflow(user_id: int, record: WorkflowRecord) -> None:
    data = _load()
    data.setdefault(str(user_id), []).append(record)
    _save(data)
    logger.info("Saved workflow %s for user %s", record["workflow_id"], user_id)


def get_user_workflows(user_id: int) -> list[WorkflowRecord]:
    return _load().get(str(user_id), [])


def update_workflow_status(user_id: int, workflow_id: str, *, active: bool) -> bool:
    data = _load()
    for rec in data.get(str(user_id), []):
        if rec["workflow_id"] == workflow_id:
            rec["active"] = active
            _save(data)
            return True
    return False


def remove_workflow(user_id: int, workflow_id: str) -> bool:
    data = _load()
    wf_list = data.get(str(user_id), [])
    new_list = [r for r in wf_list if r["workflow_id"] != workflow_id]
    if len(new_list) == len(wf_list):
        return False
    data[str(user_id)] = new_list
    _save(data)
    return True
