from datetime import datetime

from pydantic import BaseModel, Field


class OrderCreate(BaseModel):
    product_id: int = Field(gt=0)
    quantity: int = Field(gt=0)
    


class OrderUpdate(BaseModel):
    quantity: int = Field(gt=0)
    status: str = Field(min_length=1, max_length=30)


class OrderResponse(BaseModel):
    id: int
    product_id: int
    quantity: int
    price: float
    total_amount: float
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {
        "from_attributes": True
    }