"""Тесты пайплайна (освещение, ECC), Golden Board API и выравнивания."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from .conftest import designate_golden_board, login, user_id


def _png_bytes_from_bgr(img_bgr: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img_bgr)
    assert ok
    return buf.tobytes()


def _make_large_png_bytes(w: int = 800, h: int = 800) -> bytes:
    img = np.full((h, w, 3), 240, dtype=np.uint8)
    cv2.circle(img, (w // 2, h // 2), 40, (30, 30, 30), -1)
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


def test_align_ecc_improves_mae_on_shifted_synthetic() -> None:
    from app.services.alignment import align_rgb_ecc

    h, w = 160, 160
    img = np.full((h, w, 3), 235, dtype=np.uint8)
    cv2.circle(img, (w // 2, h // 2), 35, (40, 90, 140), -1)
    M = np.float32([[1.0, 0.0, 12.0], [0.0, 1.0, -9.0]])
    shifted = cv2.warpAffine(img, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    ref_g = np.mean(img, axis=2).astype(np.float32)
    mov_g = np.mean(shifted, axis=2).astype(np.float32)
    mae_before = float(np.mean(np.abs(mov_g - ref_g)))
    _, mae_after = align_rgb_ecc(shifted, img, max_iters=250, motion="affine")
    assert mae_after < mae_before * 0.92


def test_build_wled_state_body_matches_json_api_docs() -> None:
    """Формат POST /json/state как в kno.wled.ge и рабочих примерах (requests/curl)."""
    from app.services.esp32_http import build_wled_state_body

    off = build_wled_state_body(
        preset="off",
        brightness_percent=80,
        color_hex="#ffffff",
    )
    assert off["on"] is False
    assert off["v"] is True
    assert "bri" not in off or off.get("bri") != 0

    white = build_wled_state_body(
        preset="white_diffuse",
        brightness_percent=80,
        color_hex="#ffffff",
        segment_id=0,
        transition=7,
    )
    assert white["on"] is True
    assert white["bri"] == 204  # 80% of 255
    assert white["transition"] == 7
    assert white["mainseg"] == 0
    assert white["v"] is True
    seg = white["seg"][0]
    assert seg["id"] == 0
    assert seg["sel"] is True
    assert seg["fx"] == 0
    assert seg["col"][0] == [255, 255, 255]

    pink = build_wled_state_body(
        preset="rgb_highlight",
        brightness_percent=100,
        color_hex="#ffe6e9",
        segment_id=0,
    )
    assert pink["seg"][0]["col"][0] == [255, 230, 233]

    zero_bri = build_wled_state_body(
        preset="rgb_highlight",
        brightness_percent=0,
        color_hex="#ff0000",
    )
    assert zero_bri["on"] is False

    from app.services.esp32_http import apply_color_channel_order

    assert apply_color_channel_order((0, 255, 0), "rgb") == (0, 255, 0)
    assert apply_color_channel_order((0, 255, 0), "swap_gb") == (0, 0, 255)

    green_api = build_wled_state_body(
        preset="rgb_highlight",
        brightness_percent=100,
        color_hex="#00ff00",
        color_order="swap_gb",
    )
    assert green_api["seg"][0]["col"][0] == [0, 0, 255]


def test_pipeline_lighting_status_and_capture_ack(client) -> None:
    token = login(client, "operator1", "operator12345")
    headers = {"Authorization": f"Bearer {token}"}

    r = client.post(
        "/api/pipeline/lighting/preset",
        headers=headers,
        json={"preset": "rgb_highlight"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["preset"] == "rgb_highlight"
    assert r.json()["transport"] == "mock"

    r = client.get("/api/pipeline/hardware/status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["active_preset"] == "rgb_highlight"
    assert body["commands_total"] >= 1

    r = client.post("/api/pipeline/capture/ack", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json().get("ok") is True

    r = client.get("/api/pipeline/hardware/status", headers=headers)
    assert r.status_code == 200
    assert r.json()["last_capture_ack_ms"] is not None


def test_esp32_hardware_config_admin_only(client) -> None:
    op = login(client, "operator1", "operator12345")
    admin = login(client, "admin", "admin12345")
    op_h = {"Authorization": f"Bearer {op}"}
    admin_h = {"Authorization": f"Bearer {admin}"}

    r = client.get("/api/pipeline/hardware/config", headers=op_h)
    assert r.status_code == 403, r.text

    r = client.put(
        "/api/pipeline/hardware/config",
        headers=op_h,
        json={"enabled": True, "base_url": "http://192.168.0.50"},
    )
    assert r.status_code == 403, r.text

    r = client.get("/api/pipeline/hardware/config", headers=admin_h)
    assert r.status_code == 200, r.text


def test_pipeline_esp32_http_transport(client, monkeypatch) -> None:
    from app.services import esp32_http
    from app.services.esp32_http import Esp32ProbeResult, WledHttpExchange

    admin = login(client, "admin", "admin12345")
    admin_h = {"Authorization": f"Bearer {admin}"}
    op_token = login(client, "operator1", "operator12345")
    headers = {"Authorization": f"Bearer {op_token}"}

    r = client.put(
        "/api/pipeline/hardware/config",
        headers=admin_h,
        json={
            "enabled": True,
            "base_url": "http://192.168.50.130",
            "health_path": "/json/info",
            "control_path": "/json/state",
            "segment_id": 0,
            "transition": 7,
        },
    )
    assert r.status_code == 200, r.text
    cfg = r.json()
    assert cfg["health_path"] == "/json/info"
    assert cfg["control_path"] == "/json/state"

    captured: list[dict] = []

    def fake_probe(**_kwargs):
        return Esp32ProbeResult(True, 12.5, "WLED Light", {"name": "WLED Light"})

    def fake_wled_json_request(**kwargs):
        captured.append(dict(kwargs))
        return WledHttpExchange(
            ok=True,
            method=kwargs.get("method", "POST"),
            url="http://192.168.50.130/json/state",
            request_body=kwargs.get("body"),
            status_code=200,
            response_body={"state": {"on": True, "bri": 128}},
            latency_ms=3.0,
        )

    monkeypatch.setattr(esp32_http, "probe_esp32", fake_probe)
    monkeypatch.setattr(esp32_http, "wled_json_request", fake_wled_json_request)

    r = client.get("/api/pipeline/hardware/status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transport"] == "http"
    assert body["esp32_enabled"] is True
    assert body["esp32_configured"] is True
    assert body["esp32_reachable"] is True
    assert body["esp32_latency_ms"] == 12.5

    r = client.post(
        "/api/pipeline/lighting/control",
        headers=headers,
        json={"preset": "rgb_highlight", "brightness": 55, "color": "#aabbcc"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["transport"] == "http"
    assert r.json()["preset"] == "rgb_highlight"
    assert r.json()["brightness"] == 55
    assert r.json()["color"] == "#aabbcc"
    assert captured
    post_calls = [c for c in captured if c.get("method") == "POST"]
    assert post_calls
    body_sent = post_calls[-1].get("body") or {}
    assert body_sent.get("on") is True
    assert body_sent.get("bri") is not None

    r = client.post("/api/pipeline/hardware/probe", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["esp32_reachable"] is True


def test_wled_discover_and_debug_admin_only(client, monkeypatch) -> None:
    from app.services import wled_discovery
    from app.services.wled_discovery import WledDiscoverResult

    admin = login(client, "admin", "admin12345")
    admin_h = {"Authorization": f"Bearer {admin}"}
    op = login(client, "operator1", "operator12345")
    op_h = {"Authorization": f"Bearer {op}"}

    def fake_discover(**_kwargs):
        return WledDiscoverResult(
            devices=[
                {
                    "base_url": "http://192.168.50.130",
                    "ip": "192.168.50.130",
                    "name": "WLED",
                    "source": "mdns",
                    "reachable": True,
                    "latency_ms": 5.0,
                    "message": "ok",
                    "info": {"name": "WLED"},
                }
            ],
            methods_used=["mdns"],
            errors=[],
            duration_ms=10.0,
        )

    monkeypatch.setattr(wled_discovery, "discover_wled_devices", fake_discover)

    r = client.post("/api/pipeline/hardware/discover", headers=op_h, json={})
    assert r.status_code == 403, r.text

    r = client.post("/api/pipeline/hardware/discover", headers=admin_h, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["devices"]) == 1
    assert body["devices"][0]["base_url"] == "http://192.168.50.130"

    r = client.get("/api/pipeline/hardware/admin/diagnostics", headers=op_h)
    assert r.status_code == 403, r.text

    r = client.get("/api/pipeline/hardware/admin/diagnostics", headers=admin_h)
    assert r.status_code == 200, r.text


def test_pipeline_alignment_demo(client) -> None:
    token = login(client, "operator1", "operator12345")
    headers = {"Authorization": f"Bearer {token}"}
    h, w = 200, 200
    ref = np.full((h, w, 3), 230, dtype=np.uint8)
    cv2.rectangle(ref, (60, 60), (140, 140), (20, 60, 100), -1)
    M = np.float32([[1.0, 0.0, 14.0], [0.0, 1.0, -11.0]])
    mov = cv2.warpAffine(ref, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    files = [
        ("reference", ("ref.png", _png_bytes_from_bgr(ref), "image/png")),
        ("moving", ("mov.png", _png_bytes_from_bgr(mov), "image/png")),
    ]
    r = client.post("/api/pipeline/alignment/demo", headers=headers, files=files)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["mae_after"] < data["mae_before"]


def test_golden_boards_create_list_get_delete(client) -> None:
    op_token = login(client, "operator1", "operator12345")
    op_headers = {"Authorization": f"Bearer {op_token}"}
    admin_token = login(client, "admin", "admin12345")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    r = client.get("/api/golden-boards", headers=op_headers)
    assert r.status_code == 403

    payload = {"regions": [{"x1": 0, "y1": 0, "x2": 10, "y2": 10}]}
    r = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": " Эталон A ", "board_model": "BM-1", "payload": payload},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["name"] == "Эталон A"
    assert created["payload"] == payload
    assert not created.get("reference_image_url")
    pid = created["id"]

    r = client.get("/api/golden-boards", headers=admin_headers)
    assert r.status_code == 200
    assert any(row["id"] == pid for row in r.json())

    r = client.get(f"/api/golden-boards/{pid}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["payload"] == payload

    r = client.get(f"/api/golden-boards/{pid}", headers=op_headers)
    assert r.status_code == 403

    r = client.delete(f"/api/golden-boards/{pid}", headers=op_headers)
    assert r.status_code == 403

    r = client.delete(f"/api/golden-boards/{pid}", headers=admin_headers)
    assert r.status_code == 204, r.text

    r = client.get(f"/api/golden-boards/{pid}", headers=admin_headers)
    assert r.status_code == 404


def test_golden_board_choices_for_operator(client) -> None:
    admin_token = login(client, "admin", "admin12345")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    op_headers = {"Authorization": f"Bearer {login(client, 'operator1', 'operator12345')}"}
    r = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": "ChoiceTest", "payload": {"regions": [], "region_tolerance_px": 12}},
    )
    pid = r.json()["id"]
    r = client.get("/api/golden-boards/choices", headers=op_headers)
    assert r.status_code == 200
    assert not any(row["id"] == pid for row in r.json())

    op_id = user_id(client, admin_token, "operator1")
    designate_golden_board(client, admin_token, pid, op_id)

    r = client.get("/api/golden-boards/choices", headers=op_headers)
    assert r.status_code == 200
    assert any(row["id"] == pid and row["name"] == "ChoiceTest" for row in r.json())
    r = client.get("/api/golden-boards/choices", headers=admin_headers)
    assert r.status_code == 200
    assert any(row["id"] == pid for row in r.json())


def test_manager_can_access_golden_boards(client) -> None:
    admin_headers = {"Authorization": f"Bearer {login(client, 'admin', 'admin12345')}"}
    mgr = login(client, "manager1", "manager12345")
    h = {"Authorization": f"Bearer {mgr}"}
    r = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": "AdminEtalon", "payload": {"regions": [], "region_tolerance_px": 12}},
    )
    assert r.status_code == 201
    pid = r.json()["id"]
    r = client.get("/api/golden-boards", headers=h)
    assert r.status_code == 200
    assert any(row["id"] == pid and row["name"] == "AdminEtalon" for row in r.json())
    r = client.get(f"/api/golden-boards/{pid}", headers=h)
    assert r.status_code == 200
    assert r.json()["name"] == "AdminEtalon"
    r = client.post(
        "/api/golden-boards",
        headers=h,
        json={"name": "ManagerEtalon", "payload": {"regions": []}},
    )
    assert r.status_code == 201
    assert client.delete(f"/api/golden-boards/{pid}", headers=h).status_code == 403
    assert client.delete(f"/api/golden-boards/{pid}", headers=admin_headers).status_code == 204


def test_operator_cannot_access_golden_boards(client) -> None:
    op = login(client, "operator1", "operator12345")
    h = {"Authorization": f"Bearer {op}"}
    assert client.get("/api/golden-boards", headers=h).status_code == 403
    assert (
        client.post(
            "/api/golden-boards",
            headers=h,
            json={"name": "X", "payload": {"regions": []}},
        ).status_code
        == 403
    )


def test_golden_reference_markup_and_operator_blocked(client) -> None:
    admin_headers = {"Authorization": f"Bearer {login(client, 'admin', 'admin12345')}"}
    op_headers = {"Authorization": f"Bearer {login(client, 'operator1', 'operator12345')}"}

    r = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": "Markup test", "payload": {"regions": []}},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    png = _make_large_png_bytes()
    r = client.post(
        f"/api/golden-boards/{pid}/reference-image",
        headers=admin_headers,
        files={"image": ("a.png", png, "image/png")},
    )
    assert r.status_code == 200
    r = client.get(f"/api/golden-boards/{pid}/reference-image", headers=admin_headers)
    assert r.status_code == 200
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    r = client.get(f"/api/golden-boards/{pid}/reference-image", headers=op_headers)
    assert r.status_code == 403

    regions = [{"x1": 10, "y1": 10, "x2": 100, "y2": 80, "label": "roi"}]
    r = client.put(
        f"/api/golden-boards/{pid}/markup",
        headers=admin_headers,
        json={"regions": regions},
    )
    assert r.status_code == 200
    saved = r.json()["payload"]["regions"]
    assert len(saved) == 1
    assert saved[0]["x1"] == 10 and saved[0]["label"] == "roi"
    assert saved[0]["check_polarity"] is False
    assert saved[0]["polarity_kind"] == "generic"
    assert saved[0]["polarity_marker"] is None

    r = client.put(
        f"/api/golden-boards/{pid}/markup",
        headers=op_headers,
        json={"regions": []},
    )
    assert r.status_code == 403


def test_resolve_reference_path_blocks_escape(tmp_path) -> None:
    from app.services.golden_alignment import resolve_reference_path

    storage = tmp_path / "st"
    storage.mkdir()
    with pytest.raises(ValueError):
        resolve_reference_path(storage, "../evil.png")


def test_inspection_with_golden_board_ecc(client) -> None:
    admin_token = login(client, "admin", "admin12345")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    op_token = login(client, "operator1", "operator12345")
    op_headers = {"Authorization": f"Bearer {op_token}"}

    ref = np.full((800, 800, 3), 240, dtype=np.uint8)
    cv2.circle(ref, (400, 400), 45, (25, 70, 110), -1)
    ok, buf = cv2.imencode(".png", ref)
    assert ok
    ref_png = buf.tobytes()

    r = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": "E2E golden", "board_model": "E2E", "payload": {"regions": []}},
    )
    assert r.status_code == 201, r.text
    gid = r.json()["id"]

    r = client.post(
        f"/api/golden-boards/{gid}/reference-image",
        headers=admin_headers,
        files={"image": ("ref.png", ref_png, "image/png")},
    )
    assert r.status_code == 200, r.text
    assert r.json().get("reference_image_url")

    op_id = user_id(client, admin_token, "operator1")
    designate_golden_board(client, admin_token, gid, op_id)

    M = np.float32([[1.0, 0.0, 18.0], [0.0, 1.0, -14.0]])
    mov = cv2.warpAffine(ref, M, (800, 800), borderMode=cv2.BORDER_REFLECT)
    ok, mov_buf = cv2.imencode(".png", mov)
    assert ok

    res = client.post(
        "/api/inspections",
        headers=op_headers,
        files={"image": ("shifted.png", mov_buf.tobytes(), "image/png")},
        data={"golden_board_profile_id": str(gid)},
    )
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["golden_board_profile_id"] == gid
    assert data["golden_alignment_used"] is True
    assert data["alignment_mae_before"] is not None
    assert data["alignment_mae_after"] is not None
    assert data["alignment_mae_after"] < data["alignment_mae_before"]


def test_inspection_rejects_unknown_golden_profile(client) -> None:
    token = login(client, "operator1", "operator12345")
    headers = {"Authorization": f"Bearer {token}"}
    res = client.post(
        "/api/inspections",
        headers=headers,
        files={"image": ("t.png", _make_large_png_bytes(), "image/png")},
        data={"golden_board_profile_id": "999999"},
    )
    assert res.status_code == 404


def test_golden_board_designation_and_operator_filter(client) -> None:
    admin_token = login(client, "admin", "admin12345")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    op_headers = {"Authorization": f"Bearer {login(client, 'operator1', 'operator12345')}"}

    g1 = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": "Golden-A", "payload": {"regions": []}},
    ).json()
    g2 = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": "Golden-B", "payload": {"regions": []}},
    ).json()

    op_id = user_id(client, admin_token, "operator1")
    updated = designate_golden_board(client, admin_token, g1["id"], op_id)
    assert updated["designated_operator_id"] == op_id
    assert updated["designated_operator_username"] == "operator1"

    names = {row["name"] for row in client.get("/api/golden-boards/choices", headers=op_headers).json()}
    assert names == {"Golden-A"}

    mgr_token = login(client, "manager1", "manager12345")
    designate_golden_board(client, mgr_token, g2["id"], op_id)
    names = {row["name"] for row in client.get("/api/golden-boards/choices", headers=op_headers).json()}
    assert names == {"Golden-A", "Golden-B"}


def test_inspection_rejects_undesignated_golden_profile(client) -> None:
    admin_token = login(client, "admin", "admin12345")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    op_headers = {"Authorization": f"Bearer {login(client, 'operator1', 'operator12345')}"}

    gid = client.post(
        "/api/golden-boards",
        headers=admin_headers,
        json={"name": "Locked", "payload": {"regions": []}},
    ).json()["id"]

    res = client.post(
        "/api/inspections",
        headers=op_headers,
        files={"image": ("t.png", _make_large_png_bytes(), "image/png")},
        data={"golden_board_profile_id": str(gid)},
    )
    assert res.status_code == 403
