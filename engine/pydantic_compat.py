from __future__ import annotations

from typing import Any, Dict

from pydantic import BaseModel


def model_dump(model: BaseModel, **kwargs: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)  # type: ignore[attr-defined]
    return model.dict(**kwargs)  # type: ignore[no-any-return]


def model_json_schema(model_cls: type[BaseModel]) -> Dict[str, Any]:
    if hasattr(model_cls, "model_json_schema"):
        return model_cls.model_json_schema()  # type: ignore[attr-defined]
    return model_cls.schema()  # type: ignore[no-any-return]
