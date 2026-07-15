from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_create_and_read_job():
    r = client.post("/jobs", json={"goal": "Build and deploy a status page"})
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "decomposed"
    assert body["nodes"]
    job_id = body["id"]

    r2 = client.get(f"/jobs/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == job_id


def test_create_job_rejects_empty_goal():
    r = client.post("/jobs", json={"goal": ""})
    assert r.status_code == 422


def test_read_missing_job_404():
    r = client.get("/jobs/does-not-exist")
    assert r.status_code == 404
