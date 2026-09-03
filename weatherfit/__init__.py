


# .env를 패키지를 들여올 때 한 번 읽는다.
#
# 예전에는 llm.py 안에서 읽었는데, weather.py만 들여온 스크립트에서는
# 그 파일이 로드되지 않아 "KMA_API_KEY 미설정"이 떴다. 키는 넣어 뒀는데
# 안 읽히는 상황은 원인을 찾기 어렵다. 설정 로딩은 특정 모듈의 부수효과가
# 아니라 패키지의 일이다.
import os


def load_env(path: str = ".env") -> None:
    """.env를 읽어 환경변수에 채운다. 이미 있는 값은 덮지 않는다.

    python-dotenv를 의존성에 더하지 않은 것은 이 한 가지만 필요해서다.
    실제로 넣은 키가 안 읽혀서 '키가 없다'고 표시되면 한참을 헤맨다.
    """
    from pathlib import Path

    f = Path(__file__).resolve().parent.parent / path
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if v and not os.environ.get(k):
            os.environ[k] = v


load_env()
