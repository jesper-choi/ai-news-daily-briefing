#!/usr/bin/env python3
"""계층별 자체 점검. 네트워크/API 키 없이 도는 것만 담음.

    .venv/bin/python3 test_server.py
"""
import os
import re
import shutil
import time

from briefing import diagrams, llm, service, summarize

TODAY = "2026-01-01"


def reset():
    llm._model_last_call.clear()
    llm._model_exhausted.clear()


def test_top_tier_first():
    """1티어에 쿼터가 남아 있으면 절대 아래 티어로 안 내려감."""
    reset()
    for _ in range(5):
        model, _ = llm._next_model(TODAY)
        assert model == "gemini-3.7-flash", model
        llm._model_last_call[model] = 0.0  # 페이싱은 지났다고 가정


def test_falls_through_exhausted_tiers():
    reset()
    llm._model_exhausted["gemini-3.7-flash"] = TODAY
    assert llm._next_model(TODAY)[0] == "gemini-3.6-flash"
    llm._model_exhausted["gemini-3.6-flash"] = TODAY
    llm._model_exhausted["gemini-3.5-flash"] = TODAY
    assert llm._next_model(TODAY)[0].endswith("-lite")


def test_skip_moves_to_next_tier():
    """503처럼 일시적 실패로 skip된 모델이 1티어에 하나뿐일 때 아래 티어로 내려가야 함
    (안 그러면 그 모델만 계속 다시 뽑혀서 무한 재시도)."""
    reset()
    assert llm._next_model(TODAY, {"gemini-3.7-flash"})[0] == "gemini-3.6-flash"


def test_call_tally_per_day_and_model():
    """로그의 '오늘 성공 N 실패 M'이 모델별·날짜별로 따로 세어져야 함."""
    llm._model_calls.clear()
    for _ in range(3):
        llm._log_call(TODAY, "gemini-3.7-flash", 0.0, "ok", "")
    llm._log_call(TODAY, "gemini-3.7-flash", 0.0, "fail", "")
    llm._log_call(TODAY, "gemini-3.6-flash", 0.0, "ok", "")
    llm._log_call("2026-01-02", "gemini-3.7-flash", 0.0, "ok", "")
    assert llm._model_calls[(TODAY, "gemini-3.7-flash")] == [3, 1]
    assert llm._model_calls[(TODAY, "gemini-3.6-flash")] == [1, 0]
    assert llm._model_calls[("2026-01-02", "gemini-3.7-flash")] == [1, 0]
    llm._model_calls.clear()


def test_err_note_extracts_quota_id():
    """로그에서 하루치 소진(PerDay)과 잠깐 몰린 것(PerMinute)을 구분할 수 있어야 함."""
    # 실제 APIError.str()은 JSON이 아니라 dict repr이라 작은따옴표로 나온다. 처음엔 이
    # 샘플을 큰따옴표로 지어내는 바람에 테스트는 통과하는데 실제 로그는 안 잡혔음.
    per_day = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your "
               "current quota', 'details': [{'violations': [{'quotaMetric': 'generate_requests', "
               "'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}}")
    assert llm._err_note(per_day) == "GenerateRequestsPerDayPerProjectPerModel-FreeTier"
    per_minute = "429 RESOURCE_EXHAUSTED. {'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel'}"
    assert llm._err_note(per_minute) == "GenerateRequestsPerMinutePerProjectPerModel"
    assert llm._err_note("503 UNAVAILABLE. high demand") == "503 UNAVAILABLE. high demand"


def test_fetch_source_survives_a_dead_source():
    """소스 하나가 죽어도 예외가 올라가면 안 됨 - 예전엔 GeekNews 502 하나에 그날
    브리핑이 통째로 안 만들어졌음."""
    def boom():
        raise RuntimeError("502 Bad Gateway")
    assert service._fetch_source("GeekNews", boom) == []
    assert service._fetch_source("GeekNews", lambda: [{"x": 1}]) == [{"x": 1}]


def test_result_line_counts_failed_summaries():
    ok, bad = {"detail": "정상 요약"}, {"detail": summarize.SUMMARY_FAILED_MSG}
    data = {"date": "2026-01-01", "sections": [
        {"key": "geeknews", "items": [ok, bad, ok]},
        {"key": "hn", "items": [bad]},
    ]}
    line = service._result_line(data, time.time())
    assert "geeknews=3 hn=1" in line, line
    assert "항목 4 요약실패 2" in line, line


def test_d2_bakes_valid_and_drops_broken():
    """문법이 맞는 그림은 SVG로 굽고, 깨진 건 코드째 지운다(코드가 화면에 노출되면 안 됨).
    d2가 안 깔려 있으면 이 검사는 건너뜀."""
    if not diagrams.d2_bin():
        print("    (d2 미설치 - 건너뜀)")
        return
    fence = "```"
    text = (f"앞 문단.\n\n{fence}d2\n사용자 -> 서버: 요청\n서버 -> 모델\n{fence}\n\n"
            f"뒤 문단.\n\n{fence}d2\n-> -> ->\n{fence}\n\n끝 문단.")
    out = diagrams.bake_diagrams(text)
    assert out.count(f"{fence}d2svg") == 1, out[:200]      # 정상 1개만 남고
    assert f"{fence}d2\n" not in out                        # 깨진 건 흔적도 없어야
    assert "앞 문단." in out and "뒤 문단." in out and "끝 문단." in out
    block = re.search(rf"{fence}d2svg\n(.*?)\n{fence}", out, re.S).group(1)
    # 라이트/다크 한 쌍이 들어있고 페이지 CSS가 하나만 보여준다. 색을 hex로 명시하는
    # 디자인 시스템이라 d2의 --dark-theme으로는 안 되고 두 번 구워야 함.
    assert block.count('class="d2-light"') == 1 and block.count('class="d2-dark"') == 1
    svgs = re.findall(r"<svg[^>]*>", block)
    assert len(svgs) >= 2, svgs
    for tag in svgs[:1] + svgs[len(svgs) // 2:len(svgs) // 2 + 1]:
        # 폭/높이가 박혀 있어야 브라우저가 컨테이너에 맞춰 줄이지 않는다(mermaid 때의 그 문제)
        assert re.search(r'width="[\d.]+"[^>]*height="[\d.]+"', tag), tag[:200]
    assert "<?xml" not in block                             # 인라인용이라 선언 제거
    assert "class: card" not in block                       # d2 소스가 아니라 SVG여야


def test_d2_found_without_homebrew_on_path():
    """launchd로 뜬 프로세스의 PATH엔 /opt/homebrew/bin이 없다. which만 믿으면 로그인
    자동 실행 때만 다이어그램이 통째로 빠지고, 터미널에서 돌리면 멀쩡해서 못 알아챈다."""
    if not shutil.which("d2"):
        print("    (d2 미설치 - 건너뜀)")
        return
    original = os.environ["PATH"]
    try:
        os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"  # launchd가 주는 그대로
        assert shutil.which("d2") is None, "이 테스트의 전제가 깨짐(d2가 PATH에 있음)"
        assert diagrams.d2_bin() is not None, "PATH가 좁아도 d2를 찾아야 함"
    finally:
        os.environ["PATH"] = original


def test_d2_light_and_dark_differ():
    """두 번 굽는 게 의미가 있으려면 결과가 실제로 달라야 한다(프리앰블이 안 먹으면 같아짐)."""
    if not diagrams.d2_bin():
        print("    (d2 미설치 - 건너뜀)")
        return
    pair = diagrams.render_d2("a: 노드 { class: card }\nb: 다른 노드 { class: tinted }\na -> b")
    assert pair, "컴파일 실패"
    light, dark = pair
    assert light != dark
    assert "#F2F2F7" in light, "라이트 캔버스색이 없음"
    assert "#241f18" in dark, "다크 캔버스색이 없음"


def test_d2_edge_cases():
    """모델이 규칙을 살짝 어겨도 코드가 화면에 노출되면 안 된다."""
    if not diagrams.d2_bin():
        print("    (d2 미설치 - 건너뜀)")
        return
    fence = "```"
    # 대문자 태그
    up = diagrams.bake_diagrams(f"글.\n\n{fence}D2\na -> b\n{fence}")
    assert f"{fence}d2svg" in up and "D2\na -> b" not in up, up[:120]
    # 토큰 한도로 닫는 펜스 없이 잘린 경우
    cut = diagrams.bake_diagrams(f"앞 문단.\n\n{fence}d2\na -> b\nc ->")
    assert fence not in cut and "앞 문단." in cut, cut[:120]
    # abstract 자리에 그림이 오면 컴파일하지 않고 제거
    ab = diagrams.strip_diagrams(f"핵심 요약 두 문장.\n\n{fence}d2\na -> b\n{fence}")
    assert ab == "핵심 요약 두 문장." and "svg" not in ab, repr(ab)


def test_no_model_left():
    reset()
    for model, _ in llm.MODELS:
        llm._model_exhausted[model] = TODAY
    assert llm._next_model(TODAY) == (None, 0)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print(f"ok  {name}")
    reset()
    print("all passed")
