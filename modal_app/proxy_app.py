"""Modal deployment wrapper for the TokenOps proxy.

Wraps proxy.main:app as a Modal-hosted ASGI service. The proxy code
itself is unchanged — Modal handles container build, scheduling, and
TLS-terminated public URL.

Deploy with:
    modal deploy modal_app/proxy.py

Logs:
    modal app logs tokenops-proxy
"""

import modal

app = modal.App("tokenops-proxy")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements("requirements.txt")
    .add_local_python_source("proxy")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("tokenops-prod")],
    # Keep one warm container so the asyncpg pool and Qdrant client
    # singletons survive between requests. Cold start with full lifespan
    # (Neon + Qdrant + Modal embedder resolution) is ~5s — uncomfortable
    # on the hot path. Drop to 0 if cost matters more than p99.
    min_containers=1,
    # Cap autoscaling so a traffic spike does not blow past Neon free
    # tier's ~100-connection limit. 3 containers x 10 pool size = 30.
    max_containers=3,
    timeout=120,
)
@modal.asgi_app()
def fastapi_app():
    from proxy.main import app as fastapi
    return fastapi
