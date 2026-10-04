#!/bin/bash
# Start local Elasticsearch 8 (single-node, no security) for OCR/ASR search.
set -euo pipefail

ES_HOME="${ES_HOME:-/opt/elasticsearch}"
ES_VERSION="${ES_VERSION:-8.15.3}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${ES_DATA_DIR:-${REPO_DIR}/data/elasticsearch}"
LOG_DIR="${ES_LOG_DIR:-${REPO_DIR}/data/elasticsearch-logs}"
ARCH="$(uname -m)"
case "$ARCH" in
  x86_64|amd64) ES_ARCH="linux-x86_64" ;;
  aarch64|arm64) ES_ARCH="linux-aarch64" ;;
  *) echo "Unsupported arch: $ARCH" >&2; exit 1 ;;
esac

if curl -fsS http://127.0.0.1:9200 >/dev/null 2>&1; then
  echo "Elasticsearch already running on :9200"
  exit 0
fi

install_es() {
  local tarball="/tmp/elasticsearch-${ES_VERSION}-${ES_ARCH}.tar.gz"
  local url="https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-${ES_VERSION}-${ES_ARCH}.tar.gz"
  echo "Downloading Elasticsearch ${ES_VERSION} (${ES_ARCH})..."
  curl -fL --retry 3 --retry-delay 2 -o "$tarball" "$url"
  rm -rf "${ES_HOME}.extract"
  mkdir -p "${ES_HOME}.extract"
  tar -xzf "$tarball" -C "${ES_HOME}.extract"
  rm -rf "$ES_HOME"
  mv "${ES_HOME}.extract/elasticsearch-${ES_VERSION}" "$ES_HOME"
  rmdir "${ES_HOME}.extract" 2>/dev/null || true
  rm -f "$tarball"
  echo "Installed Elasticsearch to ${ES_HOME}"
}

if [ ! -x "${ES_HOME}/bin/elasticsearch" ]; then
  install_es
fi

mkdir -p "$DATA_DIR" "$LOG_DIR" /tmp/es
if ! id elasticsearch >/dev/null 2>&1; then
  useradd -r -s /usr/sbin/nologin elasticsearch
fi
chown -R elasticsearch:elasticsearch "$ES_HOME" "$DATA_DIR" "$LOG_DIR" /tmp/es

# Unprivileged containers often have vm.max_map_count=65530; disable mmap.
su -s /bin/bash elasticsearch -c "cd '${ES_HOME}' && \
  ES_JAVA_OPTS='-Xms512m -Xmx512m' \
  ES_TMPDIR=/tmp/es \
  ./bin/elasticsearch -d -p /tmp/es/es.pid \
    -Ediscovery.type=single-node \
    -Expack.security.enabled=false \
    -Expack.ml.enabled=false \
    -Eingest.geoip.downloader.enabled=false \
    -Enode.store.allow_mmap=false \
    -Enetwork.host=127.0.0.1 \
    -Ehttp.port=9200 \
    -Epath.data='${DATA_DIR}' \
    -Epath.logs='${LOG_DIR}'"

for i in $(seq 1 60); do
  if curl -fsS http://127.0.0.1:9200 >/dev/null 2>&1; then
    echo "Elasticsearch up after ${i}s"
    curl -s http://127.0.0.1:9200
    exit 0
  fi
  sleep 2
done

echo "Elasticsearch failed to start; last log lines:" >&2
tail -n 80 "${LOG_DIR}"/*.log 2>/dev/null || true
exit 1
