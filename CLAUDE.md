# AI Daily Briefing

GeekNews · Hacker News · AI Engineering 뉴스레터를 매일 훑어 AI 관련 글을 골라 한국어로
요약하고, 맥 메뉴바 앱으로 띄운 `localhost:8787` 서버에서 보여주는 1인용 도구.

## 자주 쓰는 명령

```bash
.venv/bin/python3 test_server.py        # 자체 점검 (네트워크·API 키 불필요, 빠름)
.venv/bin/python3 -m pyflakes briefing/*.py server.py test_server.py
.venv/bin/python3 server.py             # 터미널에서 직접 실행
```

배포는 별도 클론이다. 이 저장소가 워크트리라면 **여기서 고치고 → PR 머지 → 배포본이 pull**:

```bash
cd /Users/jesper/code/git/ai-news-daily-briefing && git pull --ff-only
launchctl kickstart -k gui/$(id -u)/com.jesper.ai-daily-briefing
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8787/
```

## 계층 (위가 아래를 알고, 아래는 위를 모른다)

| 모듈 | 역할 |
|---|---|
| `server.py` | 조립 — 소켓 바인드, 백그라운드 스레드 |
| `briefing/web.py` | 표현 — 페이지 HTML, HTTP 핸들러 |
| `briefing/service.py` | 응용 — 생성 흐름, "생성 중" 상태, 절전 방지 |
| `briefing/summarize.py` | 도메인 — 무엇을 고르고 어떻게 요약할지 |
| `briefing/repository.py` | 저장소 — 날짜별 캐시 파일 |
| `briefing/sources.py` | 어댑터 — 크롤링 (여기서만 `requests`/`bs4`) |
| `briefing/llm.py` | 어댑터 — Gemini (여기서만 `google.genai`) |
| `briefing/diagrams.py` | 어댑터 — d2 → SVG |
| `briefing/config.py` | 공유 — 설정과 `log()` |

**규칙**: 아래 계층에서 위 계층을 import 하지 말 것. 바깥 세계(HTTP·SDK·프로세스)는
어댑터 안에만 둘 것. 새 외부 연동은 새 어댑터로.

## 이 코드에서 반복해서 물린 것들 (고치기 전에 읽어볼 것)

- **launchd 환경은 셸과 다르다.** PATH가 `/usr/bin:/bin:/usr/sbin:/sbin`뿐이라 homebrew가
  안 보이고(그래서 `diagrams.d2_bin()`이 경로를 직접 확인함), `LANG`도 없다. 외부 실행
  파일을 새로 쓸 거면 반드시 `env -i`로 그 환경을 재현해 테스트할 것.
- **`print`에 `flush=True`가 없으면 로그가 안 보인다.** launchd가 stdout을 파일로 받으면
  블록 버퍼링이라 몇 시간씩 안 나타남. 항상 `config.log(tag, msg)`를 쓸 것.
- **맥이 자면 생성이 깨진다.** 프로세스가 얼어붙고 진행 중이던 HTTPS 연결이 끊긴다.
  요청 타임아웃으로는 못 막는다(멈춘 동안 타이머도 멈춤) → `service._keep_awake()`.
- **Gemini 무료 티어는 모델당 RPD 20.** 생성 1회가 22콜이라 한 모델로는 부족하다.
  `llm.MODEL_TIERS`가 품질 순으로 내려가며 쓰고, 하루 소진된 모델은 그날 건너뛴다.
- **부분 실패는 조용히 지나가면 안 된다.** 소스 하나가 죽어도 나머지는 만들되
  (`service._fetch_source`), 무슨 일이 있었는지는 반드시 로그에 남길 것.
- **LLM 출력은 형식을 어긴다.** d2 펜스가 대문자거나 안 닫히는 경우까지 방어되어 있음
  (`diagrams.D2_BLOCK`, `D2_DANGLING`). 새 형식을 프롬프트에 넣으면 어기는 경우도 같이 처리.

## 로그

`menubar.log`에 `[태그] MM-DD HH:MM:SS 메시지` 한 줄씩.
`[gemini]` 호출 · `[생성]` 생성 전체 · `[출처]` 크롤링 · `[요약]` 요약 · `[그림]` d2.

```bash
grep '\[생성\]' menubar.log | tail -2    # 마지막 생성이 잘 끝났나 (요약실패 0이어야)
grep '\[gemini\]' menubar.log | grep ' ok ' | awk '{print $4}' | sort | uniq -c
```

## 작업 방식

- 변경은 브랜치 → PR → squash 머지. 커밋·PR 본문은 한국어로, **왜**를 남긴다.
- 로직을 건드렸으면 `test_server.py`에 검사 하나를 남긴다. 프레임워크 없이 assert만.
- 알면서 남긴 지름길에는 `# ponytail:` 주석으로 한계와 올릴 방법을 적는다.
