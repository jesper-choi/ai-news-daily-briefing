#!/usr/bin/env python3
"""모델 선택 로직 자체 점검. 네트워크/API 키 없이 도는 것만 담음.

    .venv/bin/python3 test_server.py
"""
import time

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


def test_call_tally_per_day_and_model():
    """로그의 '오늘 성공 N 실패 M'이 모델별·날짜별로 따로 세어져야 함."""
    server._model_calls.clear()
    for _ in range(3):
        server._log_call(TODAY, "gemini-3.7-flash", 0.0, "ok", "")
    server._log_call(TODAY, "gemini-3.7-flash", 0.0, "fail", "")
    server._log_call(TODAY, "gemini-3.6-flash", 0.0, "ok", "")
    server._log_call("2026-01-02", "gemini-3.7-flash", 0.0, "ok", "")
    assert server._model_calls[(TODAY, "gemini-3.7-flash")] == [3, 1]
    assert server._model_calls[(TODAY, "gemini-3.6-flash")] == [1, 0]
    assert server._model_calls[("2026-01-02", "gemini-3.7-flash")] == [1, 0]
    server._model_calls.clear()


def test_err_note_extracts_quota_id():
    """로그에서 하루치 소진(PerDay)과 잠깐 몰린 것(PerMinute)을 구분할 수 있어야 함."""
    # 실제 APIError.str()은 JSON이 아니라 dict repr이라 작은따옴표로 나온다. 처음엔 이
    # 샘플을 큰따옴표로 지어내는 바람에 테스트는 통과하는데 실제 로그는 안 잡혔음.
    per_day = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your "
               "current quota', 'details': [{'violations': [{'quotaMetric': 'generate_requests', "
               "'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}}")
    assert server._err_note(per_day) == "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    per_minute = "429 RESOURCE_EXHAUSTED. {'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel'}"
    assert server._err_note(per_minute) == "GenerateRequestsPerMinutePerProjectPerModel"
    assert server._err_note("503 UNAVAILABLE. high demand") == "503 UNAVAILABLE. high demand"


def test_fetch_source_survives_a_dead_source():
    """소스 하나가 죽어도 예외가 올라가면 안 됨 - 예전엔 GeekNews 502 하나에 그날
    브리핑이 통째로 안 만들어졌음."""
    def boom():
        raise RuntimeError("502 Bad Gateway")
    assert server._fetch_source("GeekNews", boom) == []
    assert server._fetch_source("GeekNews", lambda: [{"x": 1}]) == [{"x": 1}]


def test_result_line_counts_failed_summaries():
    ok, bad = {"detail": "정상 요약"}, {"detail": server.SUMMARY_FAILED_MSG}
    data = {"date": "2026-01-01", "sections": [
        {"key": "geeknews", "items": [ok, bad, ok]},
        {"key": "hn", "items": [bad]},
    ]}
    line = server._result_line(data, time.time())
    assert "geeknews=3 hn=1" in line, line
    assert "항목 4 요약실패 2" in line, line


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
