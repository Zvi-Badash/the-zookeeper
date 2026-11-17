"""Optional real MinIO integration, in addition to the default mocked S3 tests."""

import os
import uuid

import boto3
import pytest
from fastapi.testclient import TestClient

from zookeeper.app import create_app
from zookeeper.demo import generate
from test_workflows import wait


@pytest.mark.skipif(
    not os.environ.get("ZK_TEST_MINIO"),
    reason="Set ZK_TEST_MINIO for a real MinIO fixture",
)
def test_real_minio_import_reopen_and_refresh(tmp_path, monkeypatch):
    endpoint = os.environ["ZK_TEST_MINIO"]
    access = "miniofixture"
    secret = "zookeeper-fixture-secret"
    monkeypatch.setenv("MINIO_ENDPOINT", endpoint)
    monkeypatch.setenv("MINIO_ACCESS_KEY", access)
    monkeypatch.setenv("MINIO_SECRET_KEY", secret)
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="us-east-1",
    )
    bucket = "zk-fixture-" + uuid.uuid4().hex[:12]
    client.create_bucket(Bucket=bucket)
    path = generate(tmp_path / "demo.parquet")
    client.put_object(Bucket=bucket, Key="demo.parquet", Body=path.read_bytes())
    with TestClient(create_app(tmp_path / "state")) as c:
        imported = c.post(
            "/api/import/minio", json={"bucket": bucket, "key": "demo.parquet"}
        ).json()
        wait(c, imported["job"])
        sid = imported["snapshot"]
    with TestClient(create_app(tmp_path / "state")) as c:
        opened = c.post(
            "/api/import/minio", json={"bucket": bucket, "key": "demo.parquet"}
        ).json()
        assert opened == {"snapshot": sid, "reused": True}
        refreshed = c.post(
            "/api/import/minio",
            json={"bucket": bucket, "key": "demo.parquet", "refresh": True},
        ).json()
        wait(c, refreshed["job"])
        assert refreshed["snapshot"] != sid
        assert c.get("/api/snapshots/" + sid).json()["status"] == "ready"
