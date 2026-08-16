"""날짜별 캐시 저장소. 브리핑 한 건 = 파일 하나."""
import glob
import json
import os

from .config import CACHE_DIR


def cache_path(day_str):
    return os.path.join(CACHE_DIR, f"{day_str}.json")

def available_dates():
    """캐시가 존재하는 날짜(YYYY-MM-DD) 목록, 최신순."""
    files = glob.glob(cache_path("*"))
    return sorted((os.path.splitext(os.path.basename(f))[0] for f in files), reverse=True)

def load_cache_for_date(day_str):
    """해당 날짜 캐시가 파일로 존재하면 로드, 없으면 None (오늘 캐시 생성은 다루지 않음 -
    그건 ensure_today_cache() / ensure_today_cache_started() 쪽 책임)."""
    path = cache_path(day_str)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None

def _save_cache(data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = cache_path(data["date"])
    tmp = path + ".tmp"
    # write to a temp file + atomic rename so concurrent readers (the polling loading page)
    # never see a half-written JSON file.
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
