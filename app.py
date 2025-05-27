# app.py
import sys, os, pathlib, asyncio, threading
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, validator
from typing import Dict
from scraper import EntradiumScraper
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = pathlib.Path(__file__).parent
if sys.platform == "win32":
    try:
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID("com.tuempresa.scrapium")
    except Exception:
        pass

class ScrapeRequest(BaseModel):
    url: str
    @validator("url")
    def must_be_http_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("La URL debe empezar por http:// o https://")
        return v

app = FastAPI(title="Scrapium API", version="1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET","POST","OPTIONS"],
    allow_headers=["*"],
)

@app.post("/scrape", response_model=Dict[str,int])
async def scrape(req: ScrapeRequest):
    try:
        loop = asyncio.get_event_loop()
        # Ejecuta run() en threadpool
        result = await loop.run_in_executor(
            None, lambda: EntradiumScraper(req.url, headless=True).run()
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.websocket("/ws/scrape")
async def websocket_scrape(ws: WebSocket):
    """
    WebSocket para streaming de (tier,stock) cada vez que se actualice.
    Cliente debe enviar {"url": "..."} al conectar.
    """
    await ws.accept()
    try:
        data = await ws.receive_json()
        url = ScrapeRequest(url=data["url"]).url  # valida esquema

        # Cola para pasar datos desde hilo a coroutine
        queue: asyncio.Queue = asyncio.Queue()

        # Worker thread que recorre run_stream y coloca eventos en la cola
        def worker():
            try:
                scraper = EntradiumScraper(url, headless=True)
                for tier, stock in scraper.run_stream():
                    asyncio.get_event_loop().call_soon_threadsafe(queue.put_nowait, (tier, stock))
            except Exception as e:
                asyncio.get_event_loop().call_soon_threadsafe(queue.put_nowait, ("__error__", str(e)))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # Consume la cola y envía por WebSocket
        while True:
            tier, stock = await queue.get()
            if tier == "__error__":
                await ws.send_json({"__error__": stock})
                break
            await ws.send_json({"tier": tier, "stock": stock})
            # Si el hilo ha terminado y la cola vacía, cerramos
            if not thread.is_alive() and queue.empty():
                break

    except WebSocketDisconnect:
        pass
    finally:
        await ws.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST","0.0.0.0"),
        port=int(os.getenv("PORT",8000)),
        log_level="info",
        lifespan="off"
    )
