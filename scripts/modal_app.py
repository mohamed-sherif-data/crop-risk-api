"""Deploy the Crop Risk service to Modal.

    py -m modal deploy scripts/modal_app.py

The public URL is ``https://crop-risk--api.modal.run`` and it is built from two
names, neither of which is a person: the Modal workspace (``crop-risk``) and the
endpoint label (``api``). Changing either moves the link and the old one stops
answering, because Modal has no redirect, so both are chosen to be readable by a
team rather than by an account.
The image installs ``requirements.txt`` (the same file Docker uses), so the
served numbers cannot drift from the local and container builds. Everything else
is mounted at container start, so a code change redeploys in seconds without a
rebuild.
"""

from __future__ import annotations

from pathlib import Path

import modal

ROOT = Path(__file__).resolve().parents[1]

image = (
    modal.Image.debian_slim(python_version="3.12")
    .workdir("/app")
    .pip_install_from_requirements(str(ROOT / "requirements.txt"))
    # Local files come last: they are mounted when a container starts, so
    # editing the UI never rebuilds the image.
    .add_local_file(ROOT / "engine.py", "/app/engine.py")
    .add_local_dir(ROOT / "api", "/app/api", ignore=["__pycache__", "*.pyc"])
    .add_local_dir(ROOT / "data", "/app/data")
    .add_local_dir(ROOT / "web", "/app/web")
)

app = modal.App("crop-risk", image=image)


@app.function(min_containers=0, max_containers=1)
@modal.asgi_app(label="api")
def crop_api():
    """The FastAPI application, exactly as served locally and in Docker."""
    from api.main import app as web_app

    return web_app

