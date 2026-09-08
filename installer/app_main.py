"""설치형 진입점. 더블클릭하면 이게 돈다.

파이썬도 터미널도 모르는 사람이 쓴다는 전제로 짰다.
  · 설정과 산출물은 사용자 폴더에 둔다 (앱 안에 쓰면 업데이트 때 다 날아간다)
  · 포트가 막혀 있으면 다음 빈 포트를 찾는다
  · 처음이면 설정 화면부터, 그다음부터는 편집기부터 연다
  · 창을 닫아도 서버가 남지 않게 한다
"""

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

if getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(getattr(sys, "_MEIPASS", ".")) / "src"))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def free_port(start: int = 8765, tries: int = 40) -> int:
    """쓸 수 있는 포트를 찾는다. 8765 는 다른 프로그램도 잘 쓴다."""
    for offset in range(tries):
        port = start + offset
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main() -> int:
    from trendletter.config import ROOT, ensure_data_root, load
    ensure_data_root()

    cfg = load()
    port = free_port(int(cfg.get("editor.port", 8765)))
    first = not cfg.settings.get("setup_done")
    url = "http://127.0.0.1:%d/%s" % (port, "setup" if first else "")

    print("동향지  —  %s" % url)
    print("설정과 산출물: %s" % ROOT)
    print("이 창을 닫으면 종료됩니다.")

    def open_later():
        # 서버가 뜨기 전에 열면 빈 화면이 뜬다. 실제로 응답할 때까지 기다린다.
        for _ in range(60):
            with socket.socket() as s:
                s.settimeout(0.3)
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.25)
        try:
            webbrowser.open(url)
        except Exception:                                  # noqa: BLE001
            pass

    threading.Thread(target=open_later, daemon=True).start()

    from trendletter.editor.app import app
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        pass
    except Exception as exc:                               # noqa: BLE001
        # 설치형에서 터지면 창이 그냥 사라진다. 무엇이 잘못됐는지는 남겨야 한다.
        import traceback
        log = Path.home() / "동향지-오류.txt"
        log.write_text(traceback.format_exc(), encoding="utf-8")
        print("\n오류가 났습니다. 내용을 %s 에 적었습니다.\n%s" % (log, exc))
        if os.name == "nt":
            input("엔터를 누르면 닫습니다.")
        raise
