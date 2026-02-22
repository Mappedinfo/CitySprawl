from __future__ import annotations

import json
from typing import Any, Dict

from engine.models import CityArtifact
from engine.pydantic_compat import model_dump


def artifact_to_json(artifact: CityArtifact, *, indent: int = 2) -> str:
    payload: Dict[str, Any] = model_dump(artifact)
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def artifact_to_json_bytes(artifact: CityArtifact) -> bytes:
    return artifact_to_json(artifact).encode("utf-8")
