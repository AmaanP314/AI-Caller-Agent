# 1. Base Image
FROM pytorch/pytorch:2.5.0-cuda12.4-cudnn9-runtime

# 2. Set working directory
WORKDIR /

# 3. Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    espeak-ng \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 4. Copy requirements and install packages
# This is layered to take advantage of Docker caching.
# We install requirements *before* copying app code.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

RUN pip install --no-cache-dir https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl

# 5. Copy the entire application code
# This copies everything inside your local 'app/' folder
# into the container's '/app' working directory.
COPY app/ /app/

# 6. Expose port (matching app/main.py)
ENV PORT=8000
EXPOSE 8000

# 7. Health check
# This points to our root "/" endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# 8. Set environment variables for caching
ENV HF_HOME=/tmp/.cache/huggingface
ENV TRANSFORMERS_CACHE=/tmp/.cache/huggingface
ENV DATABASE_URL=sqlite:////tmp/medicare_agent.db
ENV PYTHONUSERBASE=/tmp/.local
ENV PIP_CACHE_DIR=/tmp/.cache/pip

# 9. Run as non-root user
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /tmp
USER appuser

# 10. Run the main application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]