from datetime import datetime

from pydantic_v2 import BaseModel, Field, root_validator, validator


class OrderPayload(BaseModel):
    order_id: str = Field(alias="orderId")
    item_code: str = Field(alias="itemCode")
    quantity: int = Field(alias="quantity")
    requested_at: datetime = Field(alias="requestedAt")
    expedited: bool = Field(alias="expedited", default=False)

    class Config:
        allow_population_by_field_name = True
        extra = "forbid"
        strict = True
        json_encoders = {
            datetime: lambda value: value.isoformat().replace("+00:00", "Z")
        }

    @validator("item_code", pre=True)
    def normalize_item_code(cls, value):
        return value.strip().upper()

    @validator("quantity")
    def positive_quantity(cls, value):
        if value <= 0:
            raise ValueError("quantity must be positive")
        return value

    @root_validator(skip_on_failure=True)
    def expedited_batch_limit(cls, values):
        if values.get("expedited") and values.get("quantity", 0) > 10:
            raise ValueError("expedited quantity cannot exceed 10")
        return values
