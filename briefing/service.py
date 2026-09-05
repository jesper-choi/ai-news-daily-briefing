"""응용 서비스. 오늘자 브리핑을 만드는 흐름과 '지금 생성 중인가' 상태를 관리한다.
HTTP 핸들러는 여기까지만 알면 되고, 크롤링/LLM/저장소는 몰라도 된다."""
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime
from typing import NamedTuple

from .config import AUTOGEN_INTERVAL, FETCH_WORKERS, NEWSLETTER_DAYS, log
from .repository import _save_cache, load_cache_for_date
from .sources import fetch_hn_top, fetch_newsletter_recent, fetch_source_text, fetch_top20
from .summarize import SUMMARY_FAILED_MSG, select_ai_related, summarize_ko


class Source(NamedTuple):
    key: str      # 캐시 JSON과 CSS에서 쓰는 이름
    label: str    # 페이지에 보이는 섹션 제목
    fetch: object  # 후보 목록을 가져오는 함수
    pick: bool    # Gemini로 AI 관련성 상위 PICK_N개만 고를지


# 소스는 여기서만 선언한다. 새 소스를 붙이려면 sources.py에 fetch 함수를 하나 쓰고
# 이 목록에 한 줄 더하면 끝 - 예전엔 _build_today_data 안에서 가져오기·선별·섹션 조립을
# 각각 따로 적어야 해서 세 군데를 고쳐야 했다.
# 목록 순서가 곧 화면 순서다. 개수가 가장 적은 뉴스레터를 맨 위에 두고, 뒤 소스는 앞
# 소스가 이미 가져간 링크를 버린다(GeekNews가 HN 글을 재수집하는 경우가 잦음).
SOURCES = [
    Source("newsletter", f"AI Engineering · 최근 {NEWSLETTER_DAYS}일", fetch_newsletter_recent, pick=False),
    Source("geeknews", "GeekNews", fetch_top20, pick=True),
    Source("hn", "Hacker News", fetch_hn_top, pick=True),
]


def build_section(items):
    """항목 리스트에 원문 크롤링 + Gemini 요약을 채워넣는다 (in place, 리스트도 반환)."""
    if not items:
        return items
    # Article bodies are plain HTTP fetches (no rate limit) -> safe to parallelize.
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        sources = list(pool.map(fetch_source_text, items))

    # Gemini free-tier caps requests/min -> summarize sequentially (summarize_ko paces itself).
    for item, (source_text, source_kind) in zip(items, sources):
        result = summarize_ko(item, source_text)
        item["abstract"] = result["abstract"]
        item["detail"] = result["detail"]
        item["summary_source"] = source_kind
    return items

def _fetch_source(label, fetch):
    """소스 하나를 가져온다. 그 소스가 죽어 있으면 빈 리스트로 계속 진행한다 - 예전엔
    GeekNews가 502만 나도 예외가 그대로 올라가 그날 브리핑이 통째로 안 만들어졌음.
    한 소스가 죽어도 나머지 섹션은 정상으로 보여주는 게 맞다."""
    try:
        items = fetch()
        log("출처", f"{label} {len(items)}건 수집")
        return items
    except Exception as e:
        log("출처", f"{label} 수집 실패 -> 이 섹션은 비우고 진행: {type(e).__name__}: {str(e)[:80]}")
        return []

def _build_today_data():
    """오늘자 데이터를 실제로 크롤링+요약해서 만든다 (몇 분 걸림). 디스크에 쓰지 않고 반환만."""
    sections, seen_links = [], set()
    for source in SOURCES:
        items = [it for it in _fetch_source(source.label, source.fetch)
                 if it["link"] not in seen_links]
        if source.pick:
            items = select_ai_related(items)
        seen_links.update(it["link"] for it in items)
        for rank, it in enumerate(items, 1):
            it["rank"] = rank
        sections.append({"key": source.key, "label": source.label, "items": items})

    for section in sections:
        build_section(section["items"])

    return {
        "date": date.today().isoformat(),
        "generated_at": datetime.now().isoformat(),
        "sections": sections,
    }


_generation_lock = threading.Lock()

_generating = False

def _result_line(data, started):
    """생성 한 번의 결과를 한 줄로. '오늘치가 제대로 나왔나'를 이 줄 하나로 판단할 수
    있어야 함 - 요약이 몇 개 비었는지가 핵심이고, 그게 0이 아니면 다시 생성할 신호."""
    items = [it for s in data["sections"] for it in s["items"]]
    failed = sum(1 for it in items if it["detail"] == SUMMARY_FAILED_MSG)
    sections = " ".join(f"{s['key']}={len(s['items'])}" for s in data["sections"])
    return (f"{data['date']} 완료 {(time.time() - started) / 60:.1f}분 | {sections} | "
            f"항목 {len(items)} 요약실패 {failed}")

@contextmanager
def _keep_awake():
    """생성이 도는 동안 맥이 유휴 절전에 들어가지 않게 잡아둔다.

    자정 직후 자동 생성이 도는데 그때 맥은 대개 자고 있다. 자면 프로세스가 통째로
    얼어붙고 진행 중이던 HTTPS 연결이 끊겨서, 깨어난 뒤 ReadTimeout이나
    'Connection reset by peer'로 그 요약이 날아간다. 실제로 10분이면 끝날 생성이
    잠들었다 깨기를 반복하며 9시간 넘게 절반만 진행된 적이 있음. 요청 타임아웃으로는
    못 막는다 - 프로세스가 멈춰 있는 동안엔 타이머도 같이 멈추니까.

    -i는 유휴 절전만 막는다(뚜껑을 덮으면 어차피 잔다). -w로 서버 pid를 물려두면
    서버가 죽었을 때 caffeinate가 혼자 남아 맥을 계속 깨워두는 일이 없다.
    """
    try:
        proc = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError as e:  # 맥이 아니거나 caffeinate가 없으면 그냥 진행
        log("생성", f"절전 방지를 걸지 못했어요(생성은 계속): {e}")
        proc = None
    try:
        yield
    finally:
        if proc is not None:
            proc.terminate()

def ensure_today_cache_started(force=False):
    """논블로킹(HTTP 핸들러용): 오늘 캐시가 있으면 반환. 없으면(또는 force=True로 재생성
    요청이면) 백그라운드 스레드로 생성을 시작해두고 None을 반환 -> 호출부는 '생성 중'
    페이지를 즉시 보여줄 수 있다."""
    today = date.today().isoformat()
    if not force:
        cached = load_cache_for_date(today)
        if cached is not None:
            return cached

    global _generating
    with _generation_lock:
        already_running = _generating
        _generating = True

    if not already_running:
        def _run():
            global _generating
            try:
                started = time.time()
                log("생성", f"{date.today().isoformat()} 시작" + (" (다시 생성)" if force else ""))
                with _keep_awake():
                    data = _build_today_data()
                    _save_cache(data)
                log("생성", _result_line(data, started))
            except Exception as e:
                # 실패해도 서버는 안 죽음 - _generating만 풀어주면 다음 새로고침 때 재시도됨
                log("생성", f"실패, 다음 요청에서 재시도: {type(e).__name__}: {e}")
            finally:
                with _generation_lock:
                    _generating = False

        threading.Thread(target=_run, daemon=True).start()
    return None

def is_generating():
    return _generating

def _daily_autogen_loop():
    """서버가 계속 떠 있으면, 아무도 접속 안 해도 날짜가 바뀌는 순간(자정 이후) 알아서
    그날 캐시 생성을 시작해준다. 이미 있거나 생성 중이면 그냥 아무것도 안 하는 가벼운 체크."""
    while True:
        time.sleep(AUTOGEN_INTERVAL)
        try:
            ensure_today_cache_started()
        except Exception as e:
            log("생성", f"자동 생성 체크 중 오류(다음 주기에 재시도): {e}")
