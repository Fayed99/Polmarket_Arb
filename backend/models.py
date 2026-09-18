from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator

Objective = Literal['maximum_upside', 'guaranteed_profit', 'controlled_loss', 'max_worst_case', 'custom']


class LegacyOptimizerSettings(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    asset: Literal['BTC', 'ETH', 'SOL'] = 'BTC'
    budget: float = Field(100, ge=0, le=100000)
    max_loss: float = Field(1, ge=0, le=100000)
    objective: Objective = 'maximum_upside'
    custom_objective: Literal['maximum_upside', 'max_worst_case', 'guaranteed_profit'] = 'maximum_upside'
    max_deployment: Optional[float] = Field(None, ge=0, le=100000)
    min_profit: float = Field(0.01, ge=0.00001, le=100000)
    min_up_share_pct: float = Field(0, ge=0, le=100)
    max_up_share_pct: float = Field(100, ge=0, le=100)
    sync_ms: int = Field(100, ge=10, le=5000)
    stale_ms: int = Field(3000, ge=250, le=15000)
    max_slippage_pct: float = Field(2, ge=0, le=20)
    latency_ms: int = Field(350, ge=0, le=5000)
    failure_rate: float = Field(0.03, ge=0, le=1)
    liquidity_retention: float = Field(0.95, gt=0, le=1)
    max_recovery_loss: float = Field(1, ge=0, le=1000)
    paused: bool = False

    @model_validator(mode='after')
    def validate_ranges(self):
        if self.min_up_share_pct > self.max_up_share_pct:
            raise ValueError('Minimum UP share allocation must not exceed maximum')
        return self


class Settings(BaseModel):
    """Active settings. Strategy and profit policy cannot be changed by a client."""
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    strategy: Literal['strict_arbitrage'] = 'strict_arbitrage'
    asset: Literal['BTC', 'ETH', 'SOL'] = 'BTC'
    budget: float = Field(100, ge=0, le=100000)
    max_deployment: Optional[float] = Field(None, ge=0, le=100000)
    sync_ms: int = Field(100, ge=10, le=5000)
    stale_ms: int = Field(3000, ge=250, le=15000)
    max_slippage_pct: float = Field(2, ge=0, le=20)
    latency_ms: int = Field(350, ge=0, le=5000)
    failure_rate: float = Field(0.03, ge=0, le=1)
    liquidity_retention: float = Field(0.95, gt=0, le=1)
    max_recovery_loss: float = Field(1, ge=0, le=1000)
    paused: bool = False


class StrictQuoteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    decision_id: str = Field(min_length=8, max_length=100)


class ExecutionRequest(BaseModel):
    quote_id: str
    request_id: str = Field(min_length=8, max_length=80)
    scenario: Literal['normal', 'one_sided', 'liquidity', 'stale', 'unsynced', 'failed'] = 'normal'


class ManualRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    up_shares: float = Field(ge=0, le=100000)
    down_shares: float = Field(ge=0, le=100000)


class StoredRecord(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str


class ManualBuyQuoteRequest(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra='forbid')
    side: Literal['up', 'down']
    amount: float = Field(gt=0, le=100000)
    unit: Literal['dollars', 'shares'] = 'dollars'


class ManualBuyExecuteRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    quote_id: str = Field(min_length=8, max_length=100)
    request_id: str = Field(min_length=8, max_length=80)
    accept_directional_risk: Literal[True]