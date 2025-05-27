import sys
import os
import pathlib
import asyncio

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, validator
from typing import Dict
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from scraper import EntradiumScraper

# ───────────────────────────────────────────────────────────────────────────────
# Cargar variables de entorno
load_dotenv()
# ───────────────────────────────────────────────────────────────────────────────
# Detectar carpeta base (por si luego usamos _MEIPASS en bundling, opcional)
BASE_DIR = pathlib.Path(__file__).parent
# ───────────────────────────────────────────────────────────────────────────────
# Configurar AppUserModelID en Windows (no afecta aquí, pero bueno)
if sys.platform == "win32":
    try:
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.tuempresa.scrapium")
    except Exception:
        pass
# ───────────────────────────────────────────────────────────────────────────────


class ScrapeRequest(BaseModel):
    url: str

    @validator("url")
    def must_be_http_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("La URL debe empezar por http:// o https://")
        return v


app = FastAPI(
    title="Scrapium API",
    version="1.0",
    description="API para consultar stock de Entradium desde una app móvil"
)

# CORS para desarrollo, en producción restringe a tus dominios
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.post("/scrape", response_model=Dict[str, int])
async def scrape(req: ScrapeRequest):
    """
    Ejecuta el scraper para la URL recibida y devuelve
    un dict {tanda: stock}.
    """
    url = req.url  # ahora es un str puro
    loop = asyncio.get_event_loop()

    try:
        # Ejecutamos el run() en un hilo para no bloquear el event loop
        result: Dict[str, int] = await loop.run_in_executor(
            None,
            lambda: EntradiumScraper(url, headless=True).run()
        )
        return result

    except Exception as exc:
        # Devolver 500 con mensaje de error
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        log_level="info",
        # workers=1  # podrías escalar con más workers
    )
