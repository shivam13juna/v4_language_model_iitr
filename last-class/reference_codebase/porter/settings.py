"""Every setting the service has, read from the environment (PORTER_* variables, or .env) at start-up.

    PORTER_PROVIDER=ollama uvicorn porter.api:app --port 8080
"""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent     # the folder that holds porter/

# The two places the model can run, and what each one fills in.
PROVIDERS = {
    "openai": {"model": "gpt-4.1-mini", "base_url": "https://api.openai.com/v1"},
    "ollama": {"model": "gpt-oss:20b", "base_url": "http://127.0.0.1:11434/v1"},
}


# Each field is read from the environment variable PORTER_<FIELD>: model from PORTER_MODEL,
# max_model_calls from PORTER_MAX_MODEL_CALLS, and so on. A value that can't be converted to the
# field's type stops the start, naming the field.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTER_", env_file=ROOT / ".env", extra="ignore")

    # the model: the provider fills in model, base_url and api_key, unless they are set
    provider: Literal["openai", "ollama"] = "openai"
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    # validation_alias: read from OPENAI_API_KEY exactly, with no PORTER_ in front
    openai_api_key: str = Field("", validation_alias="OPENAI_API_KEY")
    temperature: float = 0.0
    model_timeout_s: float = 120.0        # one model call
    model_max_retries: int = 2            # dropped connections, 5xx and 429 only

    # data
    db_path: Path = ROOT / "online_retail.db"                # the shop's orders, read-only
    ledger_path: Path = ROOT / "porter_ledger.sqlite"        # return requests
    postgres_dsn: str = ""             # where conversations are kept; empty: in this process's memory

    # what one request may do
    max_model_calls: int = 6
    max_tool_calls: int = 8

    def model_post_init(self, _context):
        self.model = self.model or PROVIDERS[self.provider]["model"]
        self.base_url = self.base_url or PROVIDERS[self.provider]["base_url"]
        # The OpenAI key goes to OpenAI's address and nowhere else. Ollama checks no key.
        if not self.api_key:
            self.api_key = (self.openai_api_key if self.base_url.startswith(PROVIDERS["openai"]["base_url"])
                            else "ollama")


settings = Settings()
