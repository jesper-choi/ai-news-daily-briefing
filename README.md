# AI Daily Briefing

[GeekNews](https://news.hada.io/)와 [Hacker News](https://news.ycombinator.com/)를 매일 훑어서 AI 관련성 높은 글만 골라, Gemini로 한국어 요약(짧은 요약 + 상세 요약)까지 붙여 `localhost`로 보여주는 개인용 크롤러 겸 서버.

- 각 소스에서 상위 20개 후보 중 AI 관련성 순으로 10개씩 선별 (Gemini 판단)
- 원문 본문을 크롤링해 요약 (실패 시 목록 페이지 요약으로 대체)
- 날짜별로 캐싱 — 한 번 생성된 날짜는 재크롤링 없이 즉시 로드
- 페이지에서 날짜 선택 / 오늘자 다시 생성 가능

## 설치

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
brew install d2        # 요약 속 다이어그램 렌더링용
cp .env.example .env   # GOOGLE_API_KEY=... 채워넣기
```

`d2`는 요약에 들어가는 다이어그램을 생성 시점에 SVG로 굽는 데 쓴다. 없어도 서버는
정상 동작하고 다이어그램만 빠진다.

`GOOGLE_API_KEY`는 [Google AI Studio](https://aistudio.google.com/apikey)에서 발급받은 Gemini API 키. 키가 없으면 서버는 뜨지만 요약 없이 크롤링 결과만 보여준다.

## 실행

**터미널에서 바로 실행:**

```bash
.venv/bin/python3 server.py
```

브라우저에서 http://localhost:8787 접속. 오늘자 캐시가 없으면 서버가 켜지자마자 자동으로 생성을 시작한다 (보통 3~6분).

**macOS 메뉴바 앱으로 실행 (권장):**

```bash
.venv/bin/python3 menubar_app.py
```

서버가 백그라운드로 뜨고 브라우저가 자동으로 열림. 상단 메뉴바 아이콘에서 "브라우저에서 열기" / "서버 끄고 종료" 가능.

## 로그인할 때 자동으로 띄우기 (선택)

`~/Library/LaunchAgents/com.jesper.ai-daily-briefing.plist`에 아래처럼 등록해두면 로그인 시 메뉴바 앱이 자동 실행된다 (경로는 실제 클론 위치에 맞게 수정):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.jesper.ai-daily-briefing</string>
  <key>ProgramArguments</key>
  <array>
    <string>/절대/경로/.venv/bin/python3</string>
    <string>/절대/경로/menubar_app.py</string>
  </array>
  <key>WorkingDirectory</key><string>/절대/경로</string>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/절대/경로/menubar.log</string>
  <key>StandardErrorPath</key><string>/절대/경로/menubar.log</string>
</dict>
</plist>
```

`KeepAlive`는 일부러 넣지 않는다 — 넣으면 메뉴바에서 종료해도 launchd가 바로 되살려서 "종료"가 안 먹힌다.

등록/반영: `launchctl load ~/Library/LaunchAgents/com.jesper.ai-daily-briefing.plist` (수정 후에는 `unload` 후 다시 `load`).

## 사용법

- 상단 드롭다운으로 과거 날짜 선택
- 오늘 날짜에서 "↻ 다시 생성" 버튼으로 오늘자를 재크롤링 (기존 캐시 무시하고 새로 생성)
- 각 글 카드의 "전체 요약 읽기"로 상세 요약 펼쳐보기, "원문 보기"로 원문 이동

## 데이터

캐시는 `cache/YYYY-MM-DD.json`에 저장된다 (`.gitignore`에 포함, 커밋되지 않음).

## 구조

계층별로 나뉘어 있고, 위가 아래를 알고 아래는 위를 모른다.

| | 역할 |
|---|---|
| `server.py` | 조립 — 소켓 바인드, 백그라운드 스레드 기동 |
| `briefing/web.py` | 표현 — 페이지 HTML, HTTP 핸들러 |
| `briefing/service.py` | 응용 — 브리핑 생성 흐름, "생성 중" 상태, 절전 방지 |
| `briefing/summarize.py` | 도메인 — 무엇을 고르고 어떻게 요약할지의 규칙 |
| `briefing/repository.py` | 저장소 — 날짜별 캐시 파일 |
| `briefing/sources.py` | 어댑터 — 크롤링 (GeekNews / HN / 뉴스레터 / 기사 본문) |
| `briefing/llm.py` | 어댑터 — Gemini 호출, 모델 티어 로테이션, 쿼터 처리 |
| `briefing/diagrams.py` | 어댑터 — d2 코드를 SVG로 컴파일 |
| `briefing/config.py` | 공유 — 설정과 로그 |

사이트가 개편되거나 모델이 바뀌면 어댑터만, 화면이 바뀌면 `web.py`만 고치면 된다.

자체 점검: `.venv/bin/python3 test_server.py` (네트워크·API 키 없이 돈다)

## 로그 보기

`menubar.log`에 태그별로 한 줄씩 쌓인다.

```bash
grep '\[gemini\]' menubar.log          # LLM 호출: 모델 / 소요시간 / 성공여부 / 오늘 누적
grep '\[생성\]'   menubar.log          # 생성 시작·완료 (소요시간, 항목 수, 요약 실패 수)
grep '\[출처\]'   menubar.log          # 크롤링 결과와 실패
grep '\[요약\]'   menubar.log          # 개별 요약 실패
grep '\[그림\]'   menubar.log          # d2 컴파일 실패

# 오늘 모델별로 몇 번 성공했나 (RPD 소진 확인)
grep '\[gemini\]' menubar.log | grep ' ok ' | awk '{print $4}' | sort | uniq -c
```

429는 `quotaId`가 같이 찍혀서 `...PerDay...`(하루치 소진, 자정까지 못 씀)와
`...PerMinute...`(잠깐 몰린 것, 곧 풀림)를 구분할 수 있다.
