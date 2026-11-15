# WizzOS – en container som styr Docker på värddatorn.
FROM docker:27-cli AS dockercli
FROM restic/restic:0.19.1 AS restic

FROM python:3.12-slim
COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
# Backup: restic + ssh (för backup till en annan server via SFTP)
COPY --from=restic /usr/bin/restic /usr/local/bin/restic
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/wizzos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV PYTHONUNBUFFERED=1 WIZZOS_DATA=/DATA WIZZOS_HOST_ROOT=/host
EXPOSE 8899
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8899/api/state')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8899", "--proxy-headers"]
