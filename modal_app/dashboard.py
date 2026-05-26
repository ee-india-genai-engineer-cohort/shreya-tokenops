"""Modal deployment wrapper for the TokenOps dashboard.

Streamlit is not an ASGI app — it runs its own Tornado-based server.
Modal's @web_server decorator handles this by giving the function a
public URL and proxying traffic to whatever the function starts on the
specified port.

Deploy with:
    modal deploy modal_app/dashboard.py

Logs:
    modal app logs tokenops-dashboard
"""

import modal

app = modal.App("tokenops-dashboard")

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements("requirements.txt")
    .add_local_python_source("proxy", "agent", "dashboard")
)


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("tokenops-prod")],
    # Dashboard is read-only on Neon and Qdrant; one warm container
    # keeps the Streamlit session state alive and avoids cold starts
    # when you open the URL.
    min_containers=1,
    max_containers=1,
    timeout=86400,  # long-lived web server
)
@modal.web_server(8501, startup_timeout=60)
def streamlit_app():
    import subprocess
    subprocess.Popen(
        [
            "streamlit", "run", "/root/dashboard/app.py",
            "--server.port=8501",
            "--server.address=0.0.0.0",
            "--server.headless=true",
            "--browser.gatherUsageStats=false",
        ]
    )
