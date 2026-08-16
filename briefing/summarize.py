"""요약 도메인. 'AI 관련성으로 고른다', '소스 분량에 맞춰 요약한다' 같은 규칙이
여기 모여 있다. 호출 수단(어느 모델을 어떤 순서로)은 llm 계층의 관심사."""
import re

from .config import PICK_N, log
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

# LLM이 만든 mermaid는 라벨 안의 괄호·따옴표·특수문자에서 자주 깨진다 -> 문법을 좁게
# 제한해서 실패율을 낮춘다. 그래도 깨지면 브라우저 쪽에서 그 다이어그램만 조용히 뺌.
MERMAID_SPEC = (
    "구조·흐름·비교처럼 그림으로 보면 확 이해되는 내용이 있으면, 관련 문단 바로 뒤에 "
    "mermaid 다이어그램을 넣어줘. 자료가 충분히 길면 2~3개까지 좋고, 글로 충분한 "
    "내용이면 억지로 넣지 마.\n"
    "다이어그램은 '그냥 나열'이 아니라 정보를 담아야 해. 아래를 적극적으로 활용해:\n"
    "- 화살표에 라벨을 붙여 관계를 설명: A -->|\"캐시 미스\"| B\n"
    "- 단계를 subgraph로 묶어 계층을 보여주기: subgraph S1[\"학습 단계\"] ... end\n"
    "- 조건 분기는 마름모 노드로: C{\"토큰 남았나\"} -->|\"예\"| D[\"계속 생성\"]\n"
    "- 주체가 여럿이고 순서가 핵심이면 sequenceDiagram + Note over / alt 로\n"
    "- 노드는 6~14개 정도로 충분히 구체적으로. 노드 2~3개짜리 앙상한 그림은 쓰지 마\n"
    "이런 건 다이어그램으로 그리지 마(그림으로 만들면 오히려 읽기 나빠짐):\n"
    "- 벤치마크 점수·수치 비교처럼 사실상 표인 내용. 그건 그냥 글로 써\n"
    "- 관계 없이 항목만 늘어놓은 목록\n"
    "가로로 너무 넓어지지 않게: 기본은 flowchart TD로 위에서 아래로 흐르게 짜고, "
    "한 단계에 나란히 놓는 형제 노드는 3개까지만. 단계가 4개 이하로 짧고 일직선일 때만 "
    "flowchart LR을 써.\n"
    "문법은 아래를 반드시 지켜(어기면 그림이 통째로 안 그려짐):\n"
    "- ```mermaid 로 시작해서 ``` 로 닫는 코드블록으로 쓸 것\n"
    "- 종류는 flowchart TD, flowchart LR, sequenceDiagram 중 하나만 쓸 것\n"
    "- 노드 라벨, subgraph 제목, 화살표 라벨은 전부 큰따옴표로 감쌀 것\n"
    "- 따옴표 안에 괄호 ( ) [ ] { }, 따옴표, 쉼표, 콜론, <br>, 마크다운 기호를 쓰지 말 것\n"
    "- style, classDef, click, linkStyle 같은 꾸미기 문법은 쓰지 말 것\n"
    "- 라벨은 한국어로 짧게(20자 이내)\n"
    "- 다이어그램은 본문을 보조만 함. 다이어그램 없이 읽어도 이해되게 글을 써줘"
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
            + MERMAID_SPEC
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
        "아래 형식을 정확히 지켜서 응답해줘. 아래에 허용한 mermaid 블록 말고는 "
        "마크다운 기호(#, *, - 등)나 다른 설명은 넣지 마.\n\n"
        "[ABSTRACT]\n"
        "2~3문장으로 핵심만 압축한 요약\n\n"
        "[DETAIL]\n"
        f"{detail_spec} "
        "본문만 작성하고, '더 필요하신가요?' 같은 되묻는 말이나 인사말 등 대화체 멘트는 절대 넣지 마."
    )
    try:
        abstract, detail = _split_summary(_gemini_call(prompt, max_output_tokens=16000))
        return {"abstract": abstract, "detail": detail}
    except Exception as e:
        # 예외를 그대로 본문에 넣으면 429 JSON 덩어리가 요약인 척 화면에 박힘(실제로
        # 그랬음) -> 사람이 읽을 짧은 문구만 남기고 원인은 서버 로그로 보냄
        log("요약", f"실패 ({item['title'][:50]}): {type(e).__name__}: {str(e)[:80]}")
        return {"abstract": SUMMARY_FAILED_MSG, "detail": SUMMARY_FAILED_MSG}

SUMMARY_FAILED_MSG = "요약을 생성하지 못했어요. 잠시 후 '다시 생성'을 눌러주세요."
