# OrcaSlicer 切片节点（aarch64 / x86_64 通用）
#
# 为什么从零搭而不是用现成的桌面镜像：
#   matszwe02/orcaslicer-arm 那种是 KasmVNC 全桌面（4 GB，S6 一套），
#   我们要的只是 CLI 那 300 MB 的二进制 + 几个 so。
#
# 构建：
#   make appimage   # 把 AppImage 下到 vendor/
#   make build
ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 国内网络可能需要换源；官方 CI 里传 APT_MIRROR="" 即可
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
RUN set -eux; \
    if [ -n "$APT_MIRROR" ]; then \
      for f in /etc/apt/sources.list /etc/apt/sources.list.d/ubuntu.sources; do \
        [ -f "$f" ] && sed -i \
          -e "s|http://ports.ubuntu.com/ubuntu-ports|http://$APT_MIRROR/ubuntu-ports|g" \
          -e "s|http://archive.ubuntu.com/ubuntu|http://$APT_MIRROR/ubuntu|g" \
          -e "s|http://security.ubuntu.com/ubuntu|http://$APT_MIRROR/ubuntu|g" "$f" || true; \
      done; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates curl python3 python3-venv \
      libglu1-mesa libopengl0 libwebkit2gtk-4.1-0 libice6 libsm6; \
    rm -rf /var/lib/apt/lists/*

# ---- OrcaSlicer 本体 ----
# 官方 aarch64 包只有 Ubuntu24.04 构建（glibc>=2.38），所以基础镜像必须够新。
COPY vendor/OrcaSlicer.AppImage /tmp/orca.AppImage
RUN set -eux; \
    chmod +x /tmp/orca.AppImage; \
    cd /opt; \
    /tmp/orca.AppImage --appimage-extract >/dev/null; \
    mv squashfs-root orca; \
    rm -f /tmp/orca.AppImage; \
    test -x /opt/orca/bin/orca-slicer

# ---- 应用 ----
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN python3 -m venv /opt/venv
COPY requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -i "$PIP_INDEX" -r /tmp/requirements.txt \
 && rm -f /tmp/requirements.txt

WORKDIR /app
COPY app /app/app
COPY profiles /app/profiles
COPY viewer /app/viewer
COPY web /app/web

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    ORCA_BIN=/opt/orca/bin/orca-slicer \
    SLICER_PROFILE_DIR=/app/profiles \
    SLICER_DATA_DIR=/data \
    SLICER_HOME=/data/home \
    SLICER_MAX_CONCURRENCY=1 \
    SLICER_JOB_TIMEOUT=900

RUN mkdir -p /data && useradd -m -u 1000 slicer && chown -R slicer:slicer /data \
 && chmod -R a+rX /app
USER slicer

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
