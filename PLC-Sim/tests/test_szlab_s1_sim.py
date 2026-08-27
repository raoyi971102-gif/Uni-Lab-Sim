from __future__ import annotations

import json
import urllib.parse
import urllib.request

from szlab_package_runtime import SzlabPackageRuntime
from szlab_s1_sim import S1SimulationServer


def _request(
    server: S1SimulationServer,
    method: str,
    route: str,
    body: object | None = None,
) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{server.endpoint}{route}",
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def test_s1_http_adapter_models_login_material_order_and_scheduler() -> None:
    runtime = SzlabPackageRuntime()
    server = S1SimulationServer(
        port=0,
        event_sink=runtime.observe_external,
    )
    server.start()
    try:
        login = _request(server, "POST", "/auth/login", {"username": "u", "password": "p"})
        material = _request(server, "POST", "/material/add", {"name": "water"})
        order = _request(server, "POST", "/experiment/add", {"name": "run-1", "channel": 2})
        started = _request(server, "POST", "/experiment/start", [order["data"]["id"]])
        query = urllib.parse.urlencode({"channel": 2})
        realtime = _request(server, "GET", f"/experimentInformation/channel?{query}")

        assert login["data"]["token"] == "szlab-sim-token"
        assert material["data"]["id"] == 1
        assert started["code"] == "0"
        assert realtime["data"] == {"channel": 2, "status": "running"}
        snapshot = runtime.snapshot()
        assert snapshot["sequence"] == 15
        assert snapshot["coverage"]["counts"]["external"] == 16
        assert snapshot["active_runs"] == []
        assert snapshot["world"]["devices"]["s1_workstation"]["phase"] == "reset"
    finally:
        server.stop()


def test_s1_server_can_restart_and_preserves_no_socket_ownership() -> None:
    server = S1SimulationServer(port=0)
    server.start()
    first_port = server.port
    assert server.snapshot()["running"] is True
    server.stop()
    assert server.snapshot()["running"] is False

    replacement = S1SimulationServer(port=first_port)
    replacement.start()
    try:
        assert replacement.port == first_port
    finally:
        replacement.stop()
