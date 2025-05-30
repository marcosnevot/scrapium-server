import sys
import os
import pathlib
import asyncio
import threading

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, validator
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import websockets.exceptions

from scraper import EntradiumScraper

load_dotenv()
BASE_DIR = pathlib.Path(__file__).parent

if sys.platform == "win32":
    try:
        from ctypes import windll
        windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "com.tuempresa.scrapium")
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
    allow_methods=["GET", "POST", "OPTIONS"],
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
    stop_event = threading.Event()
    try:
        data = await ws.receive_json()
        url = ScrapeRequest(url=data["url"]).url

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, int] | tuple[str, str]] = asyncio.Queue()

        scraper = EntradiumScraper(url, headless=True)
        scraper.stop_event = stop_event

        # 1) extraer y enviar event_info
        event_info = scraper._scrape_event_info()
        try:
            await ws.send_json({"event_info": event_info})
        except (WebSocketDisconnect, websockets.exceptions.ConnectionClosedOK):
            stop_event.set()
            return

        # 2) worker thread streaming
        def worker():
            try:
                for tier, stock in scraper.run_stream():
                    if scraper.stop_event.is_set():
                        break
                    loop.call_soon_threadsafe(queue.put_nowait, (tier, stock))
                if not scraper.stop_event.is_set():
                    loop.call_soon_threadsafe(queue.put_nowait, ("__complete__", ""))
            except Exception as e:
                loop.call_soon_threadsafe(queue.put_nowait, ("__error__", str(e)))

        threading.Thread(target=worker, daemon=True).start()

        # 3) consumir cola y enviar datos
        while True:
            key, val = await queue.get()
            if key == "__error__":
                try:
                    await ws.send_json({"__error__": val})
                except Exception:
                    pass
                break
            if key == "__complete__":
                try:
                    await ws.send_json({"__complete__": True})
                except Exception:
                    pass
                break

            # datos de tier
            try:
                await ws.send_json({"tier": key, "stock": val})
            except (WebSocketDisconnect, websockets.exceptions.ConnectionClosedOK):
                scraper.stop_event.set()
                break

    except WebSocketDisconnect:
        stop_event.set()
    finally:
        stop_event.set()
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        log_level="info",
        lifespan="off"
    )
