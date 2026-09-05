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

from .config import AUTOGEN_INTERVAL, FETCH_WORKERS, GENERATE_HOUR, NEWSLETTER_DAYS, log

# 맥이 잔 걸 알아채는 방법: 감시 스레드가 SLEEP_TICK초마다 깨어나 시계를 본다.
# 그 사이 SLEEP_GAP초 넘게 흘렀으면 프로세스가 그동안 얼어 있었다는 뜻이다
# (자는 동안엔 스레드도 같이 멈추므로 sleep(30)이 20분처럼 보인다).
SLEEP_TICK = 30
SLEEP_GAP = 120
_slept = threading.Event()


class MachineSlept(RuntimeError):
    """생성 도중 맥이 잤다. 그대로 이어가면 끊긴 연결로 실패만 쌓이므로 접는다."""
from .repository import save_cache, load_cache_for_date
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
    _abort_if_slept()
    # Article bodies are plain HTTP fetches (no rate limit) -> safe to parallelize.
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        sources = list(pool.map(fetch_source_text, items))

    # Gemini free-tier caps requests/min -> summarize sequentially (summarize_ko paces itself).
    for item, (source_text, source_kind) in zip(items, sources):
        _abort_if_slept()
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


def _abort_if_slept():
    """맥이 잤으면 생성을 여기서 끊는다. 요약 하나 만들 때마다 확인한다."""
    if _slept.is_set():
        raise MachineSlept("맥이 자는 동안 연결이 끊겼습니다")


@contextmanager
def _watch_for_sleep():
    """맥이 자는지 지켜보는 감시 스레드. 자는 동안엔 이 스레드도 같이 멈추므로,
    깨어나서 시계를 보면 예상보다 훨씬 많이 지나 있다 - 그게 잠들었다는 신호다."""
    _slept.clear()
    stop = threading.Event()

    def tick():
        last = time.time()
        while not stop.wait(SLEEP_TICK):
            now = time.time()
            if now - last > SLEEP_GAP:
                log("생성", f"{(now - last) / 60:.0f}분 공백 - 맥이 잔 것으로 보고 생성을 접습니다")
                _slept.set()
                return
            last = now

    threading.Thread(target=tick, daemon=True).start()
    try:
        yield
    finally:
        stop.set()


@contextmanager
def _keep_awake():
    """생성이 도는 동안 맥이 자지 않게 잡아둔다.

    자정 직후 자동 생성이 도는데 그때 맥은 대개 자고 있다. 자면 프로세스가 통째로
    얼어붙고 진행 중이던 HTTPS 연결이 끊겨서, 깨어난 뒤 ReadTimeout이나
    'Connection reset by peer'로 그 요약이 날아간다. 실제로 12분이면 끝날 생성이
    잠들었다 깨기를 반복하며 11시간 걸린 날이 있었다. 요청 타임아웃으로는 못 막는다 -
    프로세스가 멈춰 있는 동안엔 타이머도 같이 멈추니까.

    -i는 유휴 절전만 막지만 -s는 시스템 절전 자체를 막는다(단 전원 연결 시에만 유효).
    -w로 서버 pid를 물려두면 서버가 죽었을 때 caffeinate가 혼자 남아 맥을 계속
    깨워두는 일이 없다.

    뚜껑을 덮는 경우(clamshell)까지 막으려면 root 권한이 필요한
    `pmset -a disablesleep 1`뿐이다. 그건 시스템 전역·영구 설정이라 켜둔 채 가방에
    넣으면 계속 돌아 발열이 생긴다 -> 코드에서 임의로 켜지 않는다(README 참고).
    """
    try:
        proc = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
        log("생성", f"절전 방지 시작 ({_power_source()})")
    except OSError as e:  # 맥이 아니거나 caffeinate가 없으면 그냥 진행
        log("생성", f"절전 방지를 걸지 못했어요(생성은 계속): {e}")
        proc = None
    try:
        yield
    finally:
        if proc is not None:
            proc.terminate()


def _power_source():
    """전원 연결 여부. -s 어설션은 AC일 때만 유효해서, 생성이 오래 걸린 날 배터리였는지를
    로그로 구분할 수 있어야 한다."""
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"],
                             capture_output=True, text=True, timeout=5).stdout
        return "AC 전원" if "AC Power" in out else "배터리 - 시스템 절전은 못 막음"
    except Exception:
        return "전원 상태 불명"


def ensure_today_cache_started(force=False):
    """논블로킹(HTTP 핸들러용): 오늘 캐시가 있으면 반환. 없으면(또는 force=True로 재생성
    요청이면) 백그라운드 스레드로 생성을 시작해두고 None을 반환 -> 호출부는 '생성 중'
    페이지를 즉시 보여줄 수 있다."""
    today = date.today().isoformat()
    if not force:
        cached = load_cache_for_date(today)
        if cached is not None:
            return cached
        # 아직 이른 시간이면 시작하지 않는다. 자정 직후엔 맥이 자고 있어서 생성이
        # 잠들었다 깨기를 반복하며 몇 시간씩 걸렸다. 지금 당장 보고 싶으면 '다시
        # 생성'(force=True)이 있다.
        if before_generate_hour():
            return None

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
                with _keep_awake(), _watch_for_sleep():
                    data = _build_today_data()
                    save_cache(data)
                log("생성", _result_line(data, started))
            except MachineSlept as e:
                # 캐시를 저장하지 않으므로 다음 확인(daily_autogen_loop, 최대 30분)이나
                # 다음 접속 때 처음부터 다시 만든다. 절반만 된 브리핑을 남기는 것보다,
                # 깨어 있을 때 12분 만에 온전히 만드는 편이 낫다.
                log("생성", f"중단: {e} ({(time.time() - started) / 60:.0f}분 진행) -> 다음에 처음부터 다시")
            except Exception as e:
                # 실패해도 서버는 안 죽음 - _generating만 풀어주면 다음 새로고침 때 재시도됨
                log("생성", f"실패, 다음 요청에서 재시도: {type(e).__name__}: {e}")
            finally:
                with _generation_lock:
                    _generating = False

        threading.Thread(target=_run, daemon=True).start()
    return None


def before_generate_hour():
    """아직 자동 생성을 시작할 시각이 아닌가. 화면에서 '생성 중'과 '예정'을 구분하는 데도 쓴다."""
    return datetime.now().hour < GENERATE_HOUR


def is_generating():
    return _generating


def daily_autogen_loop():
    """서버가 떠 있으면 아무도 접속 안 해도 그날 캐시를 알아서 만든다.
    GENERATE_HOUR 이후에만 시작하고, 이미 있거나 생성 중이면 아무것도 안 하는 가벼운 체크.
    맥이 그 시각에 꺼져 있었어도 켜진 뒤 첫 확인 때(또는 서버가 뜰 때) 돈다."""
    while True:
        time.sleep(AUTOGEN_INTERVAL)
        try:
            ensure_today_cache_started()
        except Exception as e:
            log("생성", f"자동 생성 체크 중 오류(다음 주기에 재시도): {e}")
