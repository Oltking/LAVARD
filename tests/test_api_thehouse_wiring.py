"""The API boots with TheHouse's aggregator wired, so paid Agent-to-MCP hires can route through it
for the batch discount (falling back to escrow when TheHouse can't service an ASP)."""

from fastapi.testclient import TestClient

from api.main import app


def test_lifespan_wires_thehouse_executor():
    with TestClient(app) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        # the aggregator + executor were built on the app's main loop during startup
        assert app.state.executor is not None
        assert type(app.state.executor).__name__ == "TheHouseExecutor"
        assert app.state.executor.loop is not None
        assert app.state.aggregator is not None


def test_hire_endpoint_runs_with_executor_wired():
    with TestClient(app) as client:
        view = client.post("/jobs", json={"goal": "Research rollups then write a post"}).json()
        # hire runs end to end; MCP candidates route through TheHouse, A2A via escrow, and any
        # unservable MCP call falls back to escrow — either way the endpoint returns outcomes.
        res = client.post(f"/jobs/{view['id']}/hire")
        assert res.status_code == 200
        assert isinstance(res.json(), list)
