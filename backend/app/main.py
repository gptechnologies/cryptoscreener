from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import Database
from .scanner import Scanner


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
db = Database(settings.database_path)
scanner = Scanner(settings, db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    scanner.start()
    yield
    await scanner.stop()
    db.close()


app = FastAPI(title="Dexscreener Live Scanner", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "DELETE"],
    allow_headers=["*"],
)


@app.get("/api/candidates")
async def candidates():
    return {"pairs": db.candidates()}


@app.get("/api/qualified")
async def qualified():
    return {"pairs": db.qualified()}


@app.get("/health")
async def health():
    return scanner.health()


@app.delete("/api/qualified/{pair_id}")
async def delete_qualified(pair_id: int):
    if not db.delete_qualified(pair_id):
        raise HTTPException(status_code=404, detail="Qualified pair not found")
    logging.getLogger(__name__).info("QUALIFIED_DELETED pair_id=%s", pair_id)
    return {"deleted": True}
