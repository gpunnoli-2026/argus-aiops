"""Load service modules from their dashed directories (not importable packages)."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relpath: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def correlator_module():
    return load_module("correlator_main", "services/alert-correlator/main.py")


@pytest.fixture
def correlator(correlator_module):
    correlator_module._incidents.clear()
    return correlator_module


@pytest.fixture(scope="session")
def train_module():
    return load_module("train_anomaly", "ml/training/train_anomaly.py")


@pytest.fixture(scope="session")
def llm_module():
    return load_module("llm_diagnostic", "src/llm_diagnostic.py")
