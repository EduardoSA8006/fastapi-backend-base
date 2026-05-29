from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Verifica se a aplicação está no ar."""
    return {"status": "ok"}
