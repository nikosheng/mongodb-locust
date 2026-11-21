import copy
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Literal, Optional

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


class Collections(BaseModel):
    customer: str = "digital_customer"
    tag: str = "digital_customer_tag"


class Atlas(BaseModel):
    uri: str
    database: str
    collections: Collections = Collections()
    read_preference: str = "primary"
    read_concern: str = "local"
    max_pool_size: int = 200


class TagDef(BaseModel):
    code: str
    category: str
    freq: float = Field(ge=0, le=1)


class Data(BaseModel):
    seed: int = 42
    customers: int = Field(1_000_000, gt=0)
    agents: int = 2000
    max_tags_per_customer: int = 60
    batch_size: int = 5000
    shard_size: int = 25000
    workers: int = 4
    locale: str = "en_US"
    regions: list[str]
    policy_lines: dict[str, float]
    tag_catalog: list[TagDef]

    def pool(self, name: str) -> list[str]:
        if name == "popular":
            sel = [t.code for t in self.tag_catalog if t.freq >= 0.2]
        elif name == "mid":
            sel = [t.code for t in self.tag_catalog if 0.01 <= t.freq < 0.2]
        elif name == "rare":
            sel = [t.code for t in self.tag_catalog if t.freq < 0.01]
        else:
            raise ValueError(f"unknown tag pool {name}")
        if not sel:
            raise ValueError(f"tag pool '{name}' is empty for the configured tag_catalog")
        return sel

    def fingerprint(self) -> str:
        payload = {
            "seed": self.seed,
            "customers": self.customers,
            "agents": self.agents,
            "max_tags": self.max_tags_per_customer,
            "regions": self.regions,
            "policy_lines": self.policy_lines,
            "tags": [t.model_dump() for t in self.tag_catalog],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


class ScenarioDef(BaseModel):
    pipeline: Optional[Literal["tag_first", "customer_first"]] = None
    tag_pool: Optional[Literal["popular", "mid", "rare"]] = None
    n_tags: int = Field(1, ge=1)
    mode: Literal["and", "any"] = "and"
    customer_filter: list[Literal["region", "policy_line"]] = []
    exclude_tag: bool = False
    hydrate_tags: bool = False
    lookup_style: Optional[Literal["simple", "pipeline"]] = None
    weights: Optional[dict[str, int]] = None

    @model_validator(mode="after")
    def _check(self):
        if self.weights is not None:
            return self
        if self.pipeline is None:
            raise ValueError("scenario needs either 'pipeline' or 'weights'")
        if self.pipeline == "tag_first" and not self.tag_pool:
            raise ValueError("tag_first scenarios require tag_pool")
        if self.exclude_tag and (self.pipeline != "customer_first" or not self.tag_pool):
            raise ValueError("exclude_tag requires pipeline=customer_first and a tag_pool")
        if self.pipeline == "customer_first" and not self.exclude_tag and not self.customer_filter:
            raise ValueError("customer_first scenarios require customer_filter")
        return self


class Scenario(BaseModel):
    active: str
    page_size: int = 20
    lookup_style: Literal["simple", "pipeline"] = "simple"
    definitions: dict[str, ScenarioDef]

    @model_validator(mode="after")
    def _check(self):
        if self.active not in self.definitions:
            raise ValueError(
                f"scenario.active '{self.active}' not in definitions: {sorted(self.definitions)}"
            )
        for name, d in self.definitions.items():
            if d.weights:
                for w in d.weights:
                    if w not in self.definitions or self.definitions[w].weights:
                        raise ValueError(f"'{name}' weight refers to unknown/blend scenario '{w}'")
        return self


class Load(BaseModel):
    users: int = Field(gt=0)
    duration_seconds: int = Field(gt=0)
    rps_exclude_seconds: int = Field(10, ge=0)
    spawn_rate: float = Field(10, gt=0)
    think_time_ms: int = 0
    repeats: int = 1
    workers: int = 4

    @model_validator(mode="before")
    @classmethod
    def reject_manual_warmup(cls, value):
        if isinstance(value, dict) and "warmup_seconds" in value:
            raise ValueError("Remove load.warmup_seconds: warm-up is calculated automatically")
        return value

    @property
    def warmup_seconds(self) -> int:
        return math.ceil(self.users / self.spawn_rate) + 20


class Thresholds(BaseModel):
    p95_ms: Optional[float] = None
    max_error_rate: Optional[float] = None


class Output(BaseModel):
    dir: str = "results"


class Config(BaseModel):
    atlas: Atlas
    data: Data
    scenario: Scenario
    load: Load
    thresholds: Thresholds = Thresholds()
    output: Output = Output()
    raw: dict = Field(default_factory=dict, exclude=True)


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand(v):
    if isinstance(v, str):
        def sub(m):
            name = m.group(1)
            if name not in os.environ:
                raise ValueError(f"environment variable {name} referenced in config is not set")
            return os.environ[name]
        return _ENV.sub(sub, v)
    if isinstance(v, dict):
        return {k: _expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand(x) for x in v]
    return v


def _set(d: dict, dotted: str, value):
    keys = dotted.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def load_config(path: str = "benchmark.yaml", overrides: Optional[dict] = None) -> Config:
    load_dotenv()
    raw = yaml.safe_load(Path(path).read_text())
    for k, v in (overrides or {}).items():
        if v is not None:
            _set(raw, k, v)
    cfg = Config.model_validate(_expand(copy.deepcopy(raw)))
    cfg.raw = raw
    return cfg
