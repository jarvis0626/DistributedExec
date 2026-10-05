import keyword
import math
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class RunRequest(Strict):
    code: str = Field(min_length=1, max_length=262144)
    dataset: list[Any]
    function_name: str = 'task'
    chunk_size: int = Field(default=1000, ge=1, le=1000000)
    priority: int = Field(default=0, ge=-10, le=10)
    cpu: float = Field(default=1, ge=0.1, le=128)
    memory_mb: int = Field(default=256, ge=64, le=131072)
    timeout: int = Field(default=120, ge=1, le=86400)

    @field_validator('function_name')
    @classmethod
    def valid_function(cls, value):
        if not value.isidentifier() or keyword.iskeyword(value) or value.startswith('__'):
            raise ValueError('Use a Python function identifier')
        return value


class PairRequest(Strict):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=80)
    cpu: float = Field(default=2, ge=0.1, le=128)
    memory_mb: int = Field(default=1024, ge=64, le=131072)
    concurrency: int = Field(default=1, ge=1, le=32)


class Heartbeat(Strict):
    attempts: list[str] = Field(default_factory=list, max_length=32)
    mode: Literal['accepting', 'paused', 'draining', 'stopped'] = 'accepting'
    runtime_id: str | None = Field(default=None, max_length=256)


class Completion(Strict):
    status: Literal['succeeded', 'script_error', 'invalid_result', 'timeout', 'oom', 'interrupted', 'infrastructure']
    result: list[Any] | None = None
    error: str | None = Field(default=None, max_length=32768)
    runtime_id: str = Field(min_length=1, max_length=256)
    exit_code: int | None = None
    duration: float = Field(default=0, ge=0)


class LogRequest(Strict):
    seq: int = Field(ge=0)
    stream: Literal['stdout', 'stderr', 'system'] = 'stdout'
    text: str = Field(max_length=16384)
