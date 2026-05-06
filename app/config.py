from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    anthropic_api_key: str
    voyage_api_key: str
    database_url: str

    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "ovidius-docs"

    embedding_model: str = "voyage-3"
    embedding_dimension: int = 1024
    generation_model: str = "claude-sonnet-4-6"
    rerank_model: str = "claude-haiku-4-5-20251001"

    chunk_size: int = 600
    chunk_overlap: int = 100
    retrieval_top_n: int = 20
    rerank_top_k: int = 5

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
