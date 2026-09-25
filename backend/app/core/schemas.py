"""Strict API request schemas. Domain validation remains authoritative for ledger rules."""
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str


class TimeValueStream(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=80)
    initial_value: str | int | float
    cashflow: str | int | float = "0"
    cashflow_frequency: Literal["monthly", "quarterly", "semiannual", "annual"] = "monthly"
    cashflow_timing: Literal["beginning", "end"] = "end"
    annual_rate: str | int | float
    compounding_frequency: Literal["monthly", "quarterly", "semiannual", "annual"] = "monthly"
    cashflow_start_month: int = Field(default=0, ge=0, le=1200)
    cashflow_duration_months: int | None = Field(default=None, ge=1, le=1200)


class TimeValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    calculation: Literal["future_value", "present_value"]
    initial_value: str | int | float | None = None
    cashflow: str | int | float | None = None
    cashflow_frequency: Literal["monthly", "quarterly", "semiannual", "annual"] = "monthly"
    cashflow_timing: Literal["beginning", "end"] = "end"
    annual_rate: str | int | float | None = None
    duration_years: int = Field(default=0, ge=0, le=100)
    duration_months: int = Field(default=0, ge=0, le=11)
    compounding_frequency: Literal["monthly", "quarterly", "semiannual", "annual"] = "monthly"
    cashflow_start_month: int = Field(default=0, ge=0, le=1200)
    cashflow_duration_months: int | None = Field(default=None, ge=1, le=1200)
    include_schedule: bool = True
    streams: list[TimeValueStream] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def select_single_or_multiple(self):
        if self.streams:
            if self.initial_value is not None or self.annual_rate is not None or self.cashflow is not None:
                raise ValueError("Use either streams or the single cash-flow fields, not both")
        elif self.initial_value is None or self.annual_rate is None:
            raise ValueError("initial_value and annual_rate are required when streams are not provided")
        return self


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AccountAdd(Command):
    name: str
    type: Literal["bank", "brokerage", "cpf"]
    currency: str = "SGD"
    cpf_type: str = ""


class AccountRef(Command):
    account: str


class AccountRename(AccountRef):
    name: str


class CreditAccountAdd(Command):
    name: str


class CreditCardAdd(Command):
    credit_account: str
    name: str


class DatedAmount(Command):
    amount: str
    date: str


class CashEvent(DatedAmount):
    account: str
    currency: str = "SGD"


class DescribedCashEvent(CashEvent):
    description: str | None = None


class CreditAccountAmount(DatedAmount):
    credit_account: str


class CreditActivity(CreditAccountAmount):
    card: str
    description: str


class CreditPayment(CreditAccountAmount):
    funding_account: str


class Trade(Command):
    account: str
    exchange: str
    symbol: str
    asset_class: Literal["equity", "etf"]
    currency: str
    quantity: str
    price: str
    date: str


class Split(Command):
    account: str
    exchange: str
    symbol: str
    asset_class: Literal["equity", "etf"]
    currency: str
    ratio: str
    date: str


class Transfer(DatedAmount):
    account: str
    destination: str
    currency: str
    to_currency: str | None = None
    received: str | None = None


class LoanAdd(Command):
    name: str = "HDB loan"
    principal: str
    rate: str
    installment: str
    as_of: str
    next_due: str
    opening_interest: str = "0"
    disburse_to: str | None = None


class LoanRate(Command):
    loan: str
    date: str
    rate: str


class Allocation(Command):
    account: str
    amount: str


class Repayment(DatedAmount):
    loan: str
    allocations: list[Allocation] = Field(min_length=1, max_length=50)


class Correction(Command):
    transaction: str
    changes: dict[str, Any]


class Void(Command):
    transaction: str


COMMAND_MODELS = {
    "account_add": AccountAdd,
    "account_rename": AccountRename,
    "account_archive": AccountRef,
    "credit_account_add": CreditAccountAdd,
    "credit_card_add": CreditCardAdd,
    "opening_cash": CashEvent,
    "deposit": DescribedCashEvent,
    "withdraw": DescribedCashEvent,
    "cpf_set": CashEvent,
    "credit_purchase": CreditActivity,
    "credit_refund": CreditActivity,
    "credit_payment": CreditPayment,
    "buy": Trade,
    "sell": Trade,
    "opening_holding": Trade,
    "split": Split,
    "transfer": Transfer,
    "loan_add": LoanAdd,
    "loan_rate": LoanRate,
    "repayment": Repayment,
    "correct": Correction,
    "void": Void,
}


def validate_command(command, payload):
    model = COMMAND_MODELS.get(command)
    if model is None:
        return payload
    return model.model_validate(payload).model_dump(exclude_none=True)
