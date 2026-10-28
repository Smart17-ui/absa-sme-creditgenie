"""Strict output schema - the model must return exactly this shape."""
from typing import Literal

from pydantic import BaseModel, Field

from creditgenie.feeds import SOURCES


class Transaction(BaseModel):
    date: str = Field(description="YYYY-MM-DD exactly as shown in the data, or 'Unknown'")
    description: str
    type: Literal["Income", "Expense", "Debt"]
    amount_zmw: float = Field(ge=0)
    source: Literal[SOURCES]
    document: str = Field(description='For images, the image label exactly as given, e.g. "Receipt: shop.png"; '
                                      'otherwise an empty string')


class SuspiciousDocument(BaseModel):
    document: str = Field(description='The image label exactly as given, e.g. "Receipt: shop.png"')
    reason: str = Field(description="Short, factual description of what looks wrong")


class CreditAnalysis(BaseModel):
    synthetic_transactions: list[Transaction]
    credit_score: int = Field(ge=1, le=100)
    loan_recommendation_zmw: float = Field(ge=0)
    risk_summary: str
    suspicious_documents: list[SuspiciousDocument]
