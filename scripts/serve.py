"""启动本地 web 界面。"""

from __future__ import annotations

import uvicorn

from fundamental.config import load_settings


def main() -> None:
    settings = load_settings()
    host = str(settings.get("web.host", "127.0.0.1"))
    port = int(settings.get("web.port", 8000))
    uvicorn.run("fundamental.web.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
