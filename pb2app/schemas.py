from pydantic import BaseModel


class YTAddRequest(BaseModel):
    urls: list[str]


class LabelBox(BaseModel):
    class_id: int = 0
    x_center: float
    y_center: float
    width: float
    height: float


class LabelRequest(BaseModel):
    boxes: list[LabelBox]


class SettingsUpdateRequest(BaseModel):
    values: dict[str, object]
