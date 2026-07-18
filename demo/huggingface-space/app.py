"""Hugging Face Spaces entrypoint for the hosted OMD demo."""
from __future__ import annotations

import os
import time


def log_startup(message: str, started_at: float) -> None:
    elapsed = time.monotonic() - started_at
    print(f"[omd-space] +{elapsed:.2f}s {message}", flush=True)


STARTED_AT = time.monotonic()

os.environ.setdefault("OMD_PUBLIC_DEMO", "1")
os.environ.setdefault("OMD_UI_HOST", "0.0.0.0")
os.environ.setdefault("OMD_UI_PORT", "7860")
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("OMD_PUBLIC_DEMO_MAX_UPLOAD_MB", "100")
os.environ.setdefault("OMD_PUBLIC_DEMO_OUTPUT_DIR", "/tmp/omd-public-demo")

log_startup("importing omd.ui", STARTED_AT)
from omd.ui import build_app, build_launch_kwargs  # noqa: E402

log_startup("building gradio app", STARTED_AT)

demo = build_app()
log_startup("gradio app built", STARTED_AT)


if __name__ == "__main__":
    kwargs = build_launch_kwargs(
        server_name=os.environ.get("OMD_UI_HOST", "0.0.0.0"),
        server_port=int(os.environ.get("OMD_UI_PORT", "7860")),
    )
    log_startup(f"launching gradio on {kwargs['server_name']}:{kwargs['server_port']}", STARTED_AT)
    demo.queue(max_size=8, default_concurrency_limit=1).launch(**kwargs)
