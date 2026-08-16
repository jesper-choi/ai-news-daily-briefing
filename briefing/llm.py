"""Gemini 어댑터. 모델 티어 로테이션·페이싱·일일 쿼터 소진 처리와 호출 로깅.
여기 말고는 아무도 google.genai를 직접 부르지 않는다."""
import re
import time
from datetime import date

from google import genai
from google.genai.errors import APIError

from .config import API_KEY, log


# 무료 티어 한도(AI Studio 대시보드 실측)를 한 모델로는 감당 못 함: flash 계열은
# RPD 20/RPM 5인데 생성 1회가 22콜(선별 2 + 요약 PICK_N*2)이라 한 번에 하루치가 날아감.
# -> 여러 모델을 함께 쓴다. 품질 좋은 flash를 앞에 두되, 매 호출마다 "지금 가장 빨리
# 부를 수 있는(=페이싱 대기가 가장 짧은)" 모델을 골라서 자동으로 부하가 갈라지고,
# 하루 쿼터가 소진된 모델은 그날 건너뛴다. flash 둘(40콜)을 다 써도 lite 둘(1000콜)이
# 받쳐줘서 요약이 통째로 실패하는 일이 없고, 병행 덕에 페이싱 대기도 절반으로 줄어듦.
# (초 = 60/RPM에 여유를 둔 값)
# 위 티어를 다 쓰기 전엔 아래 티어로 내려가지 않는다(품질 우선). 티어 안에서는 먼저
# 준비되는 모델을 골라 번갈아 쓰므로 대기가 절반으로 줄고 쿼터도 고르게 소모된다.
# 최신 3.7을 1티어에 단독으로 두어 하루 20콜은 전부 3.7이 맡고, 남는 2콜과 3.7이
# 과부하(503)일 때만 3.6/3.5로 내려간다.
MODEL_TIERS = [
    [("gemini-3.7-flash", 12)],                                    # RPD 20, RPM 5
    [("gemini-3.6-flash", 12), ("gemini-3.5-flash", 12)],          # RPD 20씩, RPM 5
    [("gemini-3.5-flash-lite", 4), ("gemini-3.1-flash-lite", 4)],  # RPD 500씩, RPM 15
]

MODELS = [m for tier in MODEL_TIERS for m in tier]

# 타임아웃이 없으면 응답이 안 오는 소켓에서 read()로 영영 블록된다. 실제로 그 상태로
# 17시간을 매달려 있었고, _generating이 True로 잡힌 채라 "생성 중" 화면에서 못 빠져나오고
# 자동 재시도(_daily_autogen_loop)도 계속 no-op이 됐음. 끊기면 _gemini_call이 다음
# 모델로 넘어가니 넉넉하게만 잡아주면 됨. (단위: ms)
GEMINI_TIMEOUT_MS = 240_000

gemini = (
    genai.Client(api_key=API_KEY, http_options=genai.types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))
    if API_KEY else None
)

# ponytail: 모델별 마지막 호출 시각/쿼터 소진일을 전역 dict로 들고 페이싱함. 생성은
# _generating 가드 덕에 한 번에 한 스레드만 도니까 락 없이 충분; 진짜 멀티 워커로
# 돌리려면 공유 레이트리미터(redis 등)가 필요함.
_model_last_call = {}  # model -> 마지막 호출 시각(epoch)

_model_exhausted = {}  # model -> 일일 쿼터가 소진된 날짜(YYYY-MM-DD)

def _next_model(today, skip=()):
    """쓸 모델을 고른다. 위 티어(품질 우선)에 아직 쿼터가 남아있으면 절대 아래 티어로
    내려가지 않고, 티어 안에서는 가장 빨리 준비되는 모델을 뽑는다(같으면 앞쪽 우선)."""
    now = time.time()
    for tier in MODEL_TIERS:
        best = (None, 0, float("inf"))
        for model, interval in tier:
            if model in skip or _model_exhausted.get(model) == today:
                continue
            # 이미 간격이 지난 모델은 전부 '지금 준비됨'으로 봐야 간격이 짧다는 이유만으로
            # 특정 모델이 먼저 뽑히지 않고 목록 순서(품질)대로 뽑힘
            ready_at = max(_model_last_call.get(model, 0.0) + interval, now)
            if ready_at < best[2]:
                best = (model, interval, ready_at)
        if best[0]:
            return best[0], best[1]
    return None, 0

# 모델별로 오늘 몇 번 성공/실패했는지. 무료 티어 RPD(3.7/3.6/3.5는 각 20)를 실제로
# 얼마나 썼는지 로그만 보고 알 수 있어야 해서 누적 횟수를 같이 찍는다. 날짜를 키에
# 넣어두면 자정을 넘겨도 어제 값과 안 섞이고, 프로세스가 며칠씩 떠 있어도 그대로 맞음.
_model_calls = {}  # (날짜, 모델) -> [성공, 실패]

def _err_note(e):
    """429를 로그에서 구분할 수 있게 quotaId를 뽑는다. 그냥 앞 80자를 자르면 JSON
    껍데기만 찍히고 정작 중요한 'PerDay(하루치 소진, 자정까지 못 씀)'냐
    'PerMinute(잠깐 몰린 것, 곧 풀림)'냐가 잘려나감."""
    msg = str(e)
    # APIError의 str()은 JSON이 아니라 파이썬 dict의 repr이라 따옴표가 작은따옴표다.
    # 큰따옴표만 찾다가 매번 못 잡고 앞 80자로 흘렀음 -> 둘 다 받는다.
    found = re.search(r"""['"]quotaId['"]:\s*['"]([^'"]+)['"]""", msg)
    return found.group(1) if found else msg[:80]

def _log_call(today, model, started, status, note):
    """LLM 호출 한 건을 한 줄로 남긴다. 프롬프트/응답 본문은 안 남기고 걸린 시간과
    성공 여부만. menubar.log에서 `grep '\\[gemini\\]'`로 하루치를 훑어볼 수 있음."""
    tally = _model_calls.setdefault((today, model), [0, 0])
    tally[0 if status == "ok" else 1] += 1
    ok, fail = tally
    log("gemini", f"{model} {time.time() - started:5.1f}s {status:4} (오늘 성공 {ok} 실패 {fail}) {note}")

def _gemini_call(prompt, max_output_tokens=16000):
    """여러 모델을 번갈아 쓰며 호출. 모델별 페이싱/일일 쿼터 소진/일시적 오류를 알아서
    처리하고 텍스트를 반환. 쓸 수 있는 모델이 다 떨어지면 마지막 예외를 던짐."""
    today = date.today().isoformat()
    last_error = None
    # 이번 호출에서 실패한 모델은 다시 뽑지 않는다. 티어에 모델이 하나뿐이면(3.7) 이게
    # 없을 때 그 모델이 503을 뱉는 내내 같은 모델만 계속 뽑혀 아래 티어로 못 내려감.
    failed = set()

    for _ in range(len(MODELS)):
        model, interval = _next_model(today, failed)
        if model is None:  # 오늘 쓸 수 있는 모델이 하나도 안 남음
            break

        wait = interval - (time.time() - _model_last_call.get(model, 0.0))
        if wait > 0:
            time.sleep(wait)
        _model_last_call[model] = time.time()
        started = time.time()

        try:
            # thinking_config는 안 넘김: -lite 계열은 파라미터 자체를 거부(400)하고,
            # thinking을 쓰는 모델은 그냥 쓰게 두되 아래 토큰 예산을 넉넉히 잡아 대응.
            resp = gemini.models.generate_content(
                model=model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(max_output_tokens=max_output_tokens),
            )
            text = (resp.text or "").strip()
            if text:
                _log_call(today, model, started, "ok", f"{len(text)}자")
                return text
            # 빈 응답: 내부 reasoning이 토큰 예산을 다 먹은 경우 등 -> 다른 모델로 넘어감
            _log_call(today, model, started, "fail", "빈 응답")
            last_error = RuntimeError(f"{model}이 빈 응답을 반환함")
            failed.add(model)
        except APIError as e:
            _log_call(today, model, started, "fail", f"{e.code} {_err_note(e)}")
            last_error = e
            failed.add(model)
            # 일일 쿼터 소진은 자정 전엔 안 풀리고, 4xx는 이 모델에서만 나는 요청 오류라
            # 재시도해도 같은 결과 -> 오늘은 이 모델을 빼고 다음 모델로.
            # 503 등 일시적 오류는 모델을 죽이지 않고 다음 후보로만 넘어감.
            if (e.code == 429 and "PerDay" in str(e)) or (400 <= e.code < 500 and e.code != 429):
                _model_exhausted[model] = today
        except Exception as e:
            # httpx/network-level hiccups (dropped connection, DNS blip, etc.)
            _log_call(today, model, started, "fail", f"{type(e).__name__}: {str(e)[:80]}")
            last_error = e
            failed.add(model)

    # 여기까지 왔다는 건 모든 모델이 실패했다는 뜻. 호출부가 문구로 갈음해버리면 로그에
    # 흔적이 안 남으므로 포기 사실 자체를 남긴다.
    log("gemini", f"모든 모델 실패 -> 포기 (마지막 오류: {type(last_error).__name__}: {str(last_error)[:80]})")
    raise last_error or RuntimeError("사용 가능한 Gemini 모델이 없습니다")
