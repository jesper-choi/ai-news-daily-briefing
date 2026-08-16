"""요약 도메인. 'AI 관련성으로 고른다', '소스 분량에 맞춰 요약한다' 같은 규칙이
여기 모여 있다. 호출 수단(어느 모델을 어떤 순서로)은 llm 계층의 관심사."""
import re

from .config import PICK_N, log
from .diagrams import bake_diagrams, strip_diagrams
from .llm import _gemini_call, gemini


def _split_summary(text):
    """[ABSTRACT]/[DETAIL] 마커로 응답을 분리. 마커가 없으면 앞부분을 abstract로 대체."""
    m = re.search(r"\[ABSTRACT\]\s*(.*?)\s*\[DETAIL\]\s*(.*)", text, re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    abstract = text[:180].rsplit(" ", 1)[0] + "…" if len(text) > 180 else text
    return abstract, text

def select_ai_related(items, n=PICK_N):
    """items를 AI 관련성 순으로 정렬시켜 상위 n개만 골라 반환 (Gemini 판단).
    명확히 AI 관련인 게 n개보다 적어도, 상대적으로 관련성 높은 순으로 n개를 채운다."""
    if not gemini or not items:
        return items[:n]

    listing = "\n".join(f"{i + 1}. {it['title']} - {it['excerpt']}" for i, it in enumerate(items))
    prompt = (
        f"다음은 오늘의 뉴스/토픽 {len(items)}개 목록입니다.\n\n{listing}\n\n"
        "이 목록을 'AI 관련성'이 높은 순서로 전부 정렬해줘. AI 관련성은 넓게 판단해줘 - LLM/생성형 AI/"
        "멀티모달 같은 핵심 모델·기술뿐 아니라, AI 에이전트·코딩 에이전트(예: 병렬로 코딩하는 에이전트 "
        "오픈소스 툴)·에이전틱 워크플로우·AI 개발도구·AI 인프라(파인튜닝, 추론 최적화, RAG, 벡터DB 등)·"
        "AI 정책/산업/비즈니스 동향까지 전부 AI 관련으로 포함해줘. 제목에 'AI'라는 단어가 없어도 "
        "실질적으로 AI/에이전트 기술을 다루면 관련 있는 것으로 쳐줘.\n\n"
        f"명확하게 AI 관련인 게 {n}개보다 적어도 상관없이, 상대적으로 관련성이 높아 보이는 순서로 "
        f"순위를 매겨서 상위 {n}개의 번호만 답해줘. 설명 없이 번호만 쉼표로 구분해서. 예: 3, 1, 7, 12, 5"
    )
    try:
        # 답 자체는 번호 몇 개라 짧지만, thinking을 쓰는 모델은 내부 reasoning에도 이
        # 예산을 씀 -> 200으로 조이면 생각하다 예산이 끝나 빈 응답이 옴. 넉넉히 잡아둠.
        text = _gemini_call(prompt, max_output_tokens=4000)
    except Exception as e:
        # 선별에 실패하면 AI 관련성과 무관하게 목록 앞에서 n개를 자른다. 조용히 넘어가면
        # 그날 섹션이 왜 엉뚱한 글로 찼는지 나중에 알 방법이 없어서 남긴다.
        log("생성", f"AI 관련성 선별 실패 -> 목록 상위 {n}개로 대체: {type(e).__name__}")
        return items[:n]

    picked, seen = [], set()
    for tok in re.findall(r"\d+", text):
        idx = int(tok) - 1
        if 0 <= idx < len(items) and idx not in seen:
            seen.add(idx)
            picked.append(items[idx])
        if len(picked) >= n:
            break

    # Gemini가 가끔 유효한 번호를 n개보다 적게 줌(중복/범위밖 번호를 섞어 답하거나 그냥
    # 짧게 답함) -> 그대로 두면 섹션이 5개 이하로 쪼그라듦. 원래 목록 순서대로 아직 안
    # 뽑힌 나머지로 채워서 후보가 있는 한 항상 n개를 채운다.
    if len(picked) < n:
        for i, it in enumerate(items):
            if i not in seen:
                picked.append(it)
                seen.add(i)
            if len(picked) >= n:
                break

    return picked or items[:n]

# 디자인 시스템(색·레이아웃)은 우리가 프리앰블로 주입하고(d2_preamble), 모델에겐
# '무엇을 그릴지'와 '어떤 클래스를 붙일지'만 시킨다. 노드마다 색을 지어내게 두면
# 알록달록한 '생성된 티'가 나는 그림이 되는데, 그게 이 스타일이 막으려는 바로 그것.
D2_SPEC = (
    "구조·흐름·인과처럼 그림으로 보면 확 이해되는 내용이 있으면, 관련 문단 바로 뒤에 "
    "d2 다이어그램을 넣어줘. 자료가 충분히 길면 2~3개까지 좋고, 글로 충분한 내용이면 "
    "억지로 넣지 마.\n"
    "\n"
    "무엇을 그릴지 먼저 정해:\n"
    "- 무엇이 있는가(노드), 무엇이 무엇과 통하는가(화살표), 무엇끼리 묶이는가(컨테이너)\n"
    "- 그리고 '이 그림의 주인공'을 딱 하나 고른다\n"
    "- 잎 노드가 12개를 넘으면 범위를 줄인다. 빽빽한 그림은 안 읽힌다\n"
    "\n"
    "형태는 내용에 맞춰서:\n"
    "- 단계·판단·'무슨 일이 일어나는가'  -> direction: down\n"
    "- 구성요소·'무엇으로 이루어졌는가'  -> direction: right\n"
    "- 누가 누구를 언제 호출하는가       -> shape: sequence_diagram\n"
    "\n"
    "노드마다 class를 정확히 하나씩 붙여. 색과 모양은 class가 알아서 하니 style이나 "
    "fill을 직접 쓰지 마(쓰면 통일감이 깨진다).\n"
    "  tinted   이 그림의 주인공. 딱 1개만\n"
    "  card     보통 요소 (기본값)\n"
    "  fill2    주인공 경로에 있는 보조 강조. 3개 이하\n"
    "  quiet    배경·비동기·덜 중요한 것\n"
    "  outside  외부 서비스·범위 밖\n"
    "  tray     묶음 컨테이너 전용. 2단계까지만\n"
    "  store    데이터베이스·저장소     queue  큐·브로커\n"
    "  branch   조건 분기(마름모)       pill   시작·끝\n"
    "  ok / warn / danger  실제 성공·경고·실패 상태에만. 장식으로 쓰지 말 것\n"
    "\n"
    "예시:\n"
    "```d2\n"
    "direction: down\n"
    "\n"
    "질문: 사용자 질문 { class: pill }\n"
    "캐시: 캐시 적중 { class: branch }\n"
    "검색: 검색 단계 { class: tray\n"
    "  벡터DB: 벡터 저장소 { class: store }\n"
    "  재순위: 재순위화 { class: card }\n"
    "}\n"
    "생성: LLM 생성 { class: tinted }\n"
    "응답: 응답 반환 { class: ok }\n"
    "\n"
    "질문 -> 캐시\n"
    "캐시 -> 응답: 적중\n"
    "캐시 -> 검색.벡터DB: 미스\n"
    "검색.벡터DB -> 검색.재순위\n"
    "검색.재순위 -> 생성\n"
    "생성 -> 응답\n"
    "```\n"
    "\n"
    "규칙:\n"
    "- ```d2 로 시작해 ``` 로 닫을 것\n"
    "- 이름은 짧게(20자 이내). 라벨을 따로 주려면 `키: 보이는 이름 { class: card }`\n"
    "- 이름에 콜론·따옴표·중괄호·화살표를 넣지 말 것 (콜론 뒤는 라벨 자리)\n"
    "- 화살표 라벨은 동사나 조건 한두 단어로: '미스', '실패 시', '저장'. "
    "'연결됨' 같이 아무것도 안 알려주는 말은 쓰지 말고, 뻔하면 라벨을 비워둬\n"
    "- 컨테이너 안의 노드는 점으로 가리킨다: 검색.벡터DB -> 생성\n"
    "- vars, classes, style, theme 같은 설정은 절대 쓰지 마. 우리가 붙인다\n"
    "- 벤치마크 점수 비교처럼 사실상 표인 내용, 관계 없는 목록은 그리지 마\n"
    "- 다이어그램 없이 읽어도 이해되게 글을 써줘. 그림은 보조다"
)


def summarize_ko(item, article_text):
    if not gemini:
        msg = "GOOGLE_API_KEY가 설정되지 않아 요약을 생성할 수 없습니다."
        return {"abstract": msg, "detail": msg}

    source = article_text or item["excerpt"] or item["title"]
    # 소스가 얼마나 되는지에 따라 요구 분량을 맞춘다. 예전엔 소스가 몇 줄이든 무조건
    # "4500~9000자"를 요구해서, 본문을 못 가져온 기사는 모델이 제목만 보고 지어낸 긴
    # 글이 나왔음(82자 excerpt -> 3000자 요약 같은 식). 소스가 얇으면 짧게 쓰게 하고
    # 창작을 명시적으로 금지하는 게 맞음.
    if len(source) >= 1000:
        detail_spec = (
            "A4 용지 4~6장 분량(한국어 기준 약 4500~9000자)의 아주 상세한 요약. 배경과 맥락, "
            "핵심 내용을 항목별로 풍부하게, 구체적인 근거·수치·인용·사례, 관련 배경지식, "
            "다양한 시각(찬반/한계점 등), 의의와 시사점까지 깊이 있게 다루는 여러 문단의 글. "
            "짧게 요약하지 말고 충분히 길고 읽을거리가 되도록 풀어써줘. 문단 사이는 빈 줄로 구분해줘.\n\n"
            + D2_SPEC
        )
    else:
        detail_spec = (
            "위 자료는 기사 본문이 아니라 짧은 소개글이야(본문을 가져오지 못했음). "
            "그러니 분량을 억지로 늘리지 말고, 위에 실제로 적힌 내용만으로 2~4문단 정도만 써줘. "
            "위 자료에 없는 사실·수치·인용·사례·배경을 지어내거나 추측해서 채우면 절대 안 돼. "
            "확실하지 않은 건 쓰지 말고, 아는 만큼만 담백하게 정리해줘. 문단 사이는 빈 줄로 구분해줘."
        )
    prompt = (
        f"다음은 '{item['title']}' 기사의 원문 발췌(또는 목록 페이지 요약)입니다. "
        "원문이 외국어면 자연스러운 한국어로 풀어써줘.\n\n"
        f"---\n{source}\n---\n\n"
        "아래 형식을 정확히 지켜서 응답해줘. 아래에 허용한 d2 블록 말고는 "
        "마크다운 기호(#, *, - 등)나 다른 설명은 넣지 마.\n\n"
        "[ABSTRACT]\n"
        "2~3문장으로 핵심만 압축한 요약\n\n"
        "[DETAIL]\n"
        f"{detail_spec} "
        "본문만 작성하고, '더 필요하신가요?' 같은 되묻는 말이나 인사말 등 대화체 멘트는 절대 넣지 마."
    )
    try:
        abstract, detail = _split_summary(_gemini_call(prompt, max_output_tokens=16000))
        # d2 코드를 여기서 바로 SVG로 구워 캐시에 넣는다. 페이지를 열 때마다 컴파일하면
        # 다이어그램 45개짜리 하루치가 매 요청마다 몇 초씩 걸림.
        # abstract는 카드에 한 줄로 들어가는 자리라 그림이 오면 안 된다. 모델이 거기까지
        # 다이어그램을 넣는 일은 드물지만, 들어오면 코드가 그대로 노출되므로 잘라낸다.
        return {"abstract": strip_diagrams(abstract), "detail": bake_diagrams(detail)}
    except Exception as e:
        # 예외를 그대로 본문에 넣으면 429 JSON 덩어리가 요약인 척 화면에 박힘(실제로
        # 그랬음) -> 사람이 읽을 짧은 문구만 남기고 원인은 서버 로그로 보냄
        log("요약", f"실패 ({item['title'][:50]}): {type(e).__name__}: {str(e)[:80]}")
        return {"abstract": SUMMARY_FAILED_MSG, "detail": SUMMARY_FAILED_MSG}

SUMMARY_FAILED_MSG = "요약을 생성하지 못했어요. 잠시 후 '다시 생성'을 눌러주세요."
