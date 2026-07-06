"""Modal deployment wrapper for the TokenOps optimizer agent.

Wraps agent.graph:run_optimizer as a Modal function. APScheduler is
replaced by Modal's own scheduling — but the schedule is intentionally
omitted for v1 so the agent runs only when manually triggered:

    modal run modal_app/agent.py::run

Once you've watched 2-3 manual runs and trust the behaviour, add
`schedule=modal.Period(minutes=15)` to the decorator and redeploy:

    modal deploy modal_app/agent.py

Logs:
    modal app logs tokenops-agent
"""

import modal

app = modal.App("tokenops-agent")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements("requirements.txt")
    .add_local_python_source("proxy", "agent")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("tokenops-prod")],
    # One agent run touches the analyser LLM (~10-20s) plus several
    # Postgres queries. 300s is generous headroom; Modal kills past this.
    timeout=300,
)
def run() -> dict:
    """Single optimizer pass. Returns the summary from agent.graph."""
    from agent.graph import run_optimizer
    return run_optimizer()
