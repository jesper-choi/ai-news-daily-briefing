"""설정과 공유 로그. 다른 계층이 전부 여기에만 의존한다(순환 없음)."""
import os
import time

# .env / cache/ 는 패키지 안이 아니라 프로젝트 루트에 있다. 여기서 __file__ 기준으로
# 잡으면 briefing/ 안을 보게 되므로 한 단계 올라간다.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_dotenv(path=os.path.join(ROOT, ".env")):
    """.env의 KEY=VALUE를 환경변수로 로드 (이미 설정된 값은 덮어쓰지 않음)."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_dotenv()  # API_KEY보다 먼저 돌아야 함

API_KEY = os.environ.get("GOOGLE_API_KEY")
CACHE_DIR = os.path.join(ROOT, "cache")
PORT = 8787

BASE_URL = "https://news.hada.io/"
HN_URL = "https://news.ycombinator.com/"
# 주 2회꼴로만 올라오는 뉴스레터라 '오늘'로 좁히면 대부분의 날이 비어버림 -> 최근 며칠
# 창으로 보여준다. 전부 AI 엔지니어링 글이라 AI 관련성 선별 호출은 아예 하지 않음.
NEWSLETTER_URL = "https://aiengineering.beehiiv.com/"
NEWSLETTER_DAYS = 7
CANDIDATE_N = 20  # 각 소스에서 우선 훑어볼 후보 개수
PICK_N = 10  # 그중 AI 관련성 순으로 골라낼 개수
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; news-clrawler/1.0)"}


def log(tag, msg):
    """운영 로그 한 줄. launchd가 stdout을 파일로 받으면 tty가 아니라 블록 버퍼링이
    걸려서, flush 없이 print만 하면 실패 메시지가 몇 시간씩 파일에 안 나타난다(실제로
    호출 로그[gemini]만 보이고 나머지는 안 보였음). 태그를 붙여둬서 grep으로 종류별로
    뽑을 수 있음: [gemini] 호출, [생성] 생성 전체, [출처] 크롤링, [요약] 요약."""
    print(f"[{tag}] {time.strftime('%m-%d %H:%M:%S')} {msg}", flush=True)
