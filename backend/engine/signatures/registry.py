from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import unicodedata


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return "".join(ch for ch in without_marks.casefold() if ch.isalnum())


@dataclass(frozen=True, slots=True)
class WatermarkSignature:
    id: str
    aliases: tuple[str, ...]
    typical_regions: tuple[str, ...] = ()
    repetition_expected: bool = True


class SignatureRegistry:
    def __init__(self, signatures: tuple[WatermarkSignature, ...]):
        self.signatures = signatures

    @classmethod
    def load_default(cls) -> "SignatureRegistry":
        root = Path(__file__).resolve().parent
        signatures = []
        for path in sorted(root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            signatures.append(
                WatermarkSignature(
                    id=str(payload["id"]),
                    aliases=tuple(str(item) for item in payload.get("aliases", ())),
                    typical_regions=tuple(str(item) for item in payload.get("typical_regions", ())),
                    repetition_expected=bool(payload.get("repetition_expected", True)),
                )
            )
        return cls(tuple(signatures))

    def match_text(self, text: str) -> tuple[WatermarkSignature, ...]:
        normalized = _normalize(text)
        matches = []
        for signature in self.signatures:
            if any(_normalize(alias) in normalized for alias in signature.aliases):
                matches.append(signature)
        return tuple(matches)
