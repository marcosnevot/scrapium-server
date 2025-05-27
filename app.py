import sys
import os
import pathlib
import asyncio
import threading

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, validator
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from scraper import EntradiumScraper

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

@app.post("/scrape", response_model=dict)
async def scrape(req: ScrapeRequest):
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None, lambda: EntradiumScraper(req.url, headless=True).run()
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.websocket("/ws/scrape")
async def websocket_scrape(ws: WebSocket):
    await ws.accept()
    try:
        data = await ws.receive_json()
        url = ScrapeRequest(url=data["url"]).url

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, int]|tuple[str,str]] = asyncio.Queue()

        scraper = EntradiumScraper(url, headless=True)

        # Nueva línea: Enviar event_info inicialmente
        event_info = scraper._scrape_event_info()
        await ws.send_json({"event_info": event_info})

        def worker():
            try:
                for tier, stock in scraper.run_stream():
                    loop.call_soon_threadsafe(queue.put_nowait, (tier, stock))
                loop.call_soon_threadsafe(queue.put_nowait, ("__complete__", ""))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("__error__", str(e)))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while True:
            key, val = await queue.get()
            if key == "__error__":
                await ws.send_json({"__error__": val})
                break
            if key == "__complete__":
                await ws.send_json({"__complete__": True})
                break
            await ws.send_json({"tier": key, "stock": val})

    except WebSocketDisconnect:
        pass
    finally:
        await ws.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        log_level="info",
        lifespan="off"
    )
