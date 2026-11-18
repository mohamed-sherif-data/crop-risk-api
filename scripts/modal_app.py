"""Deploy the Crop Risk service to Modal, on the existing public label.

    py -m modal deploy scripts/modal_app.py

The endpoint label is ``crop-api``, so the public URL stays
``https://mohamed-sherif-data--crop-api.modal.run`` — same link, new service.
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

app = modal.App("egypt-crop-risk", image=image)


@app.function(min_containers=0, max_containers=1)
@modal.asgi_app(label="crop-api")
def crop_api():
    """The FastAPI application, exactly as served locally and in Docker."""
    from api.main import app as web_app

    return web_app
