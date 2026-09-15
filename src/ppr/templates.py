"""同梱のカテゴリテンプレートを読む。実行時にJTSをimportしない。"""

from __future__ import annotations

import json
from pathlib import Path

from .domain import TopicTemplate

RESOURCE_DIR = Path(__file__).resolve().parent / "resources" / "topic_templates"
DEFAULT_TEMPLATE_ID = "jts-194-v1"


def load_bundled_template(template_id: str = DEFAULT_TEMPLATE_ID) -> TopicTemplate:
    path = RESOURCE_DIR / f"{template_id.replace('-', '_')}.json"
    if not path.exists():
        candidates = sorted(p.name for p in RESOURCE_DIR.glob("*.json"))
        raise FileNotFoundError(f"テンプレートが見つかりません: {template_id}（同梱: {candidates}）")
    return TopicTemplate.from_json(json.loads(path.read_text(encoding="utf-8")))
