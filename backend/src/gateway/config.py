import os

from pydantic import BaseModel, Field


class GatewayConfig(BaseModel):
    """Configuration for the API Gateway."""

    host: str = Field(default="0.0.0.0", description="Host to bind the gateway server")
    port: int = Field(default=8001, description="Port to bind the gateway server")
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:2026",
            "http://127.0.0.1:2026",
        ],
        description="Allowed CORS origins",
    )


_gateway_config: GatewayConfig | None = None


def get_gateway_config() -> GatewayConfig:
    """Get gateway config, loading from environment if available."""
    global _gateway_config
    if _gateway_config is None:
        default_origins = ",".join(GatewayConfig().cors_origins)
        cors_origins_str = os.getenv("CORS_ORIGINS", default_origins)
        _gateway_config = GatewayConfig(
            host=os.getenv("GATEWAY_HOST", "0.0.0.0"),
            port=int(os.getenv("GATEWAY_PORT", "8001")),
            cors_origins=[
                origin.strip()
                for origin in cors_origins_str.split(",")
                if origin.strip()
            ],
        )
    return _gateway_config
