FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    HF_HUB_DISABLE_TELEMETRY=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    GRADIO_TEMP_DIR=/tmp/omd-gradio \
    OMD_PUBLIC_DEMO=1 \
    OMD_UI_HOST=0.0.0.0 \
    OMD_UI_PORT=7860 \
    PORT=7860 \
    OMD_PUBLIC_DEMO_OUTPUT_DIR=/tmp/omd-public-demo \
    OMD_PUBLIC_DEMO_MAX_UPLOAD_MB=100

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user \
    && mkdir -p /home/user/app /tmp/omd-gradio /tmp/omd-public-demo \
    && chown -R user:user /home/user/app /tmp/omd-gradio /tmp/omd-public-demo

USER user
WORKDIR /home/user/app

COPY --chown=user requirements.txt /home/user/app/requirements.txt
RUN python -m pip install --no-cache-dir --user -r /home/user/app/requirements.txt

COPY --chown=user src /home/user/app/src
RUN python -m pip install --no-cache-dir --user --no-deps /home/user/app/src

COPY --chown=user app.py /home/user/app/app.py

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/', timeout=3).read(1)"]
CMD ["python", "app.py"]
