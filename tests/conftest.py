import os
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values


# Shared test setup for public API URL + backend imports
ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _public_base_url() -> str:
    env_url = os.environ.get("REACT_APP_BACKEND_URL")
    if env_url:
        return env_url.rstrip("/")
    values = dotenv_values("/app/frontend/.env")
    url = values.get("REACT_APP_BACKEND_URL")
    if not url:
        raise RuntimeError("REACT_APP_BACKEND_URL is required for public endpoint tests")
    return str(url).rstrip("/")


@pytest.fixture(scope="session")
def base_url() -> str:
    return _public_base_url()


@pytest.fixture(scope="session")
def api_url(base_url: str) -> str:
    return f"{base_url}/api"
