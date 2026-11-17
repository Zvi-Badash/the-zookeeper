ARG BASE_IMAGE=zookeeper-buildbase:cuda12.6-py312
FROM ${BASE_IMAGE}
ARG VERSION=0.1.0
LABEL org.opencontainers.image.title="The Zookeeper" org.opencontainers.image.version=${VERSION}
ENV PATH=/opt/venv/bin:$PATH ZK_DATA=/data PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/data/numba-cache MPLCONFIGDIR=/data/matplotlib \
    OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 NUMBA_NUM_THREADS=4
WORKDIR /app
COPY wheelhouse /wheelhouse
COPY requirements.lock /app/requirements.lock
RUN pip install --no-index --find-links=/wheelhouse --require-hashes -r requirements.lock && rm -rf /wheelhouse
COPY zookeeper /app/zookeeper
COPY frontend/dist /app/frontend/dist
RUN mkdir /data && chown -R 10001:10001 /data /app
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --start-period=20s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "zookeeper.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
