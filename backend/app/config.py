from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ROOT / ".env"), extra="ignore")

    access_password: str = ""
    host: str = "0.0.0.0"
    port: int = 8787
    tz: str = "Asia/Shanghai"
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-flash"
    llm_api_key: str = ""
    github_username: str = ""
    github_token: str = ""
    wecom_corpid: str = ""
    wecom_secret: str = ""
    wecom_agentid: str = ""
    wecom_touser: str = "@all"
    public_base_url: str = ""
    brave_api_key: str = ""
    tavily_api_key: str = ""
    image_api_base_url: str = ""
    image_api_key_file: str = ""
    image_api_model: str = "doubao-seedream-5.0-lite"
    database_path: str = ""

    @property
    def data_dir(self) -> Path:
        if self.database_path:
            path = Path(self.database_path).parent
        elif "onedrive" in str(ROOT).lower():
            # SQLite + OneDrive file locks hang writes; keep the DB on a local disk.
            path = Path.home() / "AppData" / "Local" / "yandu"
        else:
            path = ROOT / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def db_path(self) -> Path:
        if self.database_path:
            p = Path(self.database_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        return self.data_dir / "yandu.db"


settings = Settings()
