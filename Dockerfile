FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

WORKDIR /srv

# 构建期可指定 PyPI 镜像源。国内服务器建议：
#   --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
#   --build-arg PIP_TRUSTED_HOST=mirrors.aliyun.com
ARG PIP_INDEX_URL=https://pypi.org/simple/
ARG PIP_TRUSTED_HOST=

# opencv 运行期依赖
RUN apt-get update \
 && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN if [ -n "$PIP_TRUSTED_HOST" ]; then \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" --trusted-host "$PIP_TRUSTED_HOST" -r requirements.txt; \
    else \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt; \
    fi

COPY app ./app
COPY config.json ./config.json
COPY tools ./tools

RUN mkdir -p /srv/data

EXPOSE 7070

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7070"]
