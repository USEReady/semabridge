import os
import datetime
from pathlib import Path
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
import dotenv

router = APIRouter()

# Always point to the main project .env file
ENV_PATH = Path("D:/sema/.env")

class SecretPayload(BaseModel):
    key: str
    value: str

@router.get("/api/secrets")
def list_secrets():
    secrets = []
    if ENV_PATH.exists():
        parsed = dotenv.dotenv_values(ENV_PATH)
        mtime = os.path.getmtime(ENV_PATH)
        updated_at = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc).isoformat()
        
        for k, v in parsed.items():
            if not v:
                continue
            # Treat keys containing typical secret keywords as secrets
            if any(x in k.upper() for x in ["KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"]):
                hint = "sk-..." + v[-4:] if len(v) > 8 else "***"
                secrets.append({
                    "key": k,
                    "hint": hint,
                    "updated_at": updated_at
                })
    return secrets

@router.post("/api/secrets")
def save_secret(payload: SecretPayload):
    # Ensure file exists
    if not ENV_PATH.exists():
        ENV_PATH.touch()
        
    dotenv.set_key(ENV_PATH, payload.key, payload.value)
    
    hint = "sk-..." + payload.value[-4:] if len(payload.value) > 8 else "***"
    updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return {
        "key": payload.key,
        "hint": hint,
        "updated_at": updated_at
    }

@router.delete("/api/secrets/{key}")
def delete_secret(key: str):
    if ENV_PATH.exists():
        dotenv.unset_key(ENV_PATH, key)
    return {"status": "ok"}
