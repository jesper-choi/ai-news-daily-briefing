#!/usr/bin/env python3
"""서버를 메뉴바(상단 status bar) 앱으로 띄운다. 실행하면 서버가 백그라운드로 뜨고
브라우저가 자동으로 열림. 메뉴바 아이콘에서 다시 열기/종료 가능 (종료하면 서버도 같이 내려감).

사용법:
    .venv/bin/python3 menubar_app.py
"""
import os
import threading
import webbrowser

import rumps

import server


ICON = os.path.join(os.path.dirname(__file__), "menubar_icon.png")


class BriefingApp(rumps.App):
    def __init__(self):
        # template=True: 흑백 실루엣을 macOS가 라이트/다크 메뉴바에 맞춰 자동으로 반전해줌
        # (이모지 대신 써서 다른 메뉴바 아이콘들과 톤이 맞음, favicon과 같은 모노그램 컨셉)
        super().__init__("AI Daily Briefing", icon=ICON, template=True, quit_button="서버 끄고 종료")

    @rumps.clicked("브라우저에서 열기")
    def open_browser(self, _):
        webbrowser.open(f"http://localhost:{server.PORT}")


if __name__ == "__main__":
    # 바인드는 메인 스레드에서 먼저: 포트 충돌 등으로 실패하면 (예 - 이미 떠있는 인스턴스와
    # 겹침) 백그라운드 스레드 안에서 조용히 죽는 대신 여기서 바로 알아채고 사용자에게 보여줌
    try:
        httpd = server.start_server()
    except OSError as e:
        rumps.alert(
            title="AI Daily Briefing",
            message=f"서버를 시작하지 못했어요.\n\n{e}\n\n"
                    f"포트 {server.PORT}를 다른 프로세스가 이미 쓰고 있는지 확인해주세요.",
        )
        raise SystemExit(1)

    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    webbrowser.open(f"http://localhost:{server.PORT}")
    BriefingApp().run()
