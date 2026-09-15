"""人格の版（revision）。

`revision_id` は保存済み PersonaDocument の canonical JSON の SHA-256。

revisionの対象に入れないもの：音声binding、scheme、model設定、生成日時、作者メモ。
同じ内容なら同じ revision を再利用する。役割やモデルを変えただけで人格の版は変わらない。

この識別子は「同じ設定を使用した証拠」に限る。人格の同一性を数値で証明するものではない。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import PersonaDocument


def canonical_json(value: Any) -> str:
    """キー順を固定し、配列順を保ち、NaNを禁じた正規表現。

    `PersonaDocument.to_json()` がすでにキー順を固定しているが、入れ子の dict
    （extensions、supplements.data）は作者が任意のキーを持ちうるため sort_keys で揃える。
    """
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def revision_id_for(document: PersonaDocument) -> str:
    payload = canonical_json(document.to_json())
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Revision:
    revision_id: str
    document: PersonaDocument

    @property
    def persona_id(self) -> str:
        return self.document.persona_id

    @property
    def locale(self) -> str:
        return self.document.locale


class RevisionStore:
    """`data/revisions/<revision_id>.json` に不変の版を保存する。"""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def path_for(self, revision_id: str) -> Path:
        return self.root / f"{revision_id}.json"

    def create(self, document: PersonaDocument) -> Revision:
        revision_id = revision_id_for(document)
        path = self.path_for(revision_id)
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            payload = canonical_json(document.to_json())
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
        return Revision(revision_id=revision_id, document=document)

    def load(self, revision_id: str) -> Revision:
        path = self.path_for(revision_id)
        raw = json.loads(path.read_text(encoding="utf-8"))
        document = PersonaDocument.from_json(raw)
        actual = revision_id_for(document)
        if actual != revision_id:
            raise ValueError(
                f"revisionの内容がIDと一致しません: 要求={revision_id} 実際={actual}"
            )
        return Revision(revision_id=revision_id, document=document)

    def exists(self, revision_id: str) -> bool:
        return self.path_for(revision_id).exists()
