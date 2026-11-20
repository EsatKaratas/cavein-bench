import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:  # dotenv is optional
    pass

BIFROST_URL = os.getenv("BIFROST_URL", "http://localhost:8080").rstrip("/")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "cavein.db"
SCENARIO_DIR = ROOT / "scenarios"
AGENTS_FILE = ROOT / "agents.yaml"
TAVILY_CACHE = DATA_DIR / "tavily_cache.json"
AGENT_LOG_DIR = DATA_DIR / "agent_logs"

# The judge is a fixed model (temperature 0) so scores are comparable across agents.
# A different model from the agents under test, to limit self-preference bias.
JUDGE_MODEL = os.getenv("CAVEIN_JUDGE_MODEL", "gemini/gemini-3.5-flash-lite")
JUDGE_FALLBACKS = [m for m in os.getenv("CAVEIN_JUDGE_FALLBACKS",
                                        "gemini/gemma-4-26b-a4b-it,gemini/gemma-4-31b-it").split(",") if m]

# A model name that does not exist on the primary provider. Used by the
# "model_unavailable" fault: the request fails and Bifrost must fail over.
OUTAGE_MODEL = os.getenv("CAVEIN_OUTAGE_MODEL", "gemini/cavein-model-that-does-not-exist")

MAX_ACTIONS_PER_TURN = 8
AGENT_TIMEOUT_S = 180
