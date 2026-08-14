#!/usr/bin/env python3
"""모델 선택 로직 자체 점검. 네트워크/API 키 없이 도는 것만 담음.

    .venv/bin/python3 test_server.py
"""
import server

TODAY = "2026-01-01"


def reset():
    server._model_last_call.clear()
    server._model_exhausted.clear()


def test_top_tier_first():
    """1티어에 쿼터가 남아 있으면 절대 아래 티어로 안 내려감."""
    reset()
    for _ in range(5):
        model, _ = server._next_model(TODAY)
        assert model == "gemini-3.7-flash", model
        server._model_last_call[model] = 0.0  # 페이싱은 지났다고 가정


def test_falls_through_exhausted_tiers():
    reset()
    server._model_exhausted["gemini-3.7-flash"] = TODAY
    assert server._next_model(TODAY)[0] == "gemini-3.6-flash"
    server._model_exhausted["gemini-3.6-flash"] = TODAY
    server._model_exhausted["gemini-3.5-flash"] = TODAY
    assert server._next_model(TODAY)[0].endswith("-lite")


def test_skip_moves_to_next_tier():
    """503처럼 일시적 실패로 skip된 모델이 1티어에 하나뿐일 때 아래 티어로 내려가야 함
    (안 그러면 그 모델만 계속 다시 뽑혀서 무한 재시도)."""
    reset()
    assert server._next_model(TODAY, {"gemini-3.7-flash"})[0] == "gemini-3.6-flash"


def test_no_model_left():
    reset()
    for model, _ in server.MODELS:
        server._model_exhausted[model] = TODAY
    assert server._next_model(TODAY) == (None, 0)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    reset()
    print("all passed")
