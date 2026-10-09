import hashlib
import hmac
import secrets
import time
import warnings
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from app.core.config import get_settings
from app.core.errors import DomainError
from app.modules.references.models import StorageObject
from app.modules.workspace.service import get_project, iso, DEMO_USER

MAX_BYTES = 10 * 1024 * 1024
SIGNING_KEY = secrets.token_bytes(32)
Image.MAX_IMAGE_PIXELS = 64_000_000

def get_storage(db, identifier, project_id=None):
    s = db.get(StorageObject, str(identifier))
    if not s:
        raise DomainError(404, "NOT_FOUND", "File not found.")
    get_project(db, s.project_id)
    if project_id is not None and s.project_id != project_id:
        raise DomainError(422, "CROSS_PROJECT_REFERENCE", "The file belongs to another project.")
    return s

def signature(identifier, expiry):
    return hmac.new(SIGNING_KEY, (str(identifier) + ":" + str(expiry)).encode(), hashlib.sha256).hexdigest()

def file_url(s):
    expiry = int(time.time()) + 900
    base = get_settings().api_public_base_url.rstrip("/")
    return base + "/api/v1/files/" + s.id + "?expires=" + str(expiry) + "&signature=" + signature(s.id, expiry)

def file_path(s):
    root = Path(get_settings().storage_local_dir).resolve()
    path = (root / s.storage_key).resolve()
    if not path.is_relative_to(root):
        raise DomainError(404, "NOT_FOUND", "File not found.")
    return path

def storage_dto(s):
    url = file_url(s)
    expiry = int(url.split("expires=")[1].split("&")[0])
    return {"id": s.id, "originalFilename": s.original_filename, "mimeType": s.mime_type,
        "byteSize": s.byte_size, "width": s.width, "height": s.height, "checksum": s.checksum,
        "url": url, "urlExpiresAt": iso(datetime.fromtimestamp(expiry, timezone.utc)), "createdAt": iso(s.created_at)}

def upload(db, project_id, content, filename):
    if len(content) > MAX_BYTES:
        raise DomainError(413, "PAYLOAD_TOO_LARGE", "Image exceeds the 10 MB limit.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                kind = image.format
                width, height = image.size
                if kind not in ("PNG", "JPEG", "WEBP"):
                    raise DomainError(415, "UNSUPPORTED_MEDIA_TYPE", "Use PNG, JPEG, or WebP images.")
                if width > 8000 or height > 8000:
                    raise DomainError(400, "VALIDATION_ERROR", "Image dimensions exceed 8000 pixels per side.",
                        [{"path": "body.file", "message": "Maximum dimension is 8000 pixels."}])
                image.verify()
    except DomainError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise DomainError(415, "UNSUPPORTED_MEDIA_TYPE", "The file is not a valid PNG, JPEG, or WebP image.")
    extension = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}[kind]
    s = StorageObject(id=str(uuid4()), project_id=project_id, storage_key=str(uuid4()) + extension,
        original_filename=(Path((filename or "").replace("\\", "/")).name[:255] or None),
        mime_type={"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[kind],
        byte_size=len(content), width=width, height=height, checksum=hashlib.sha256(content).hexdigest(), uploaded_by=DEMO_USER)
    path = file_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    try:
        db.add(s)
        db.flush()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return s
