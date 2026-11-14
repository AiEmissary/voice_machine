import re
from difflib import SequenceMatcher
from typing import Dict, List


def normalize_text(text: str = "") -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", str(text or "").lower())).strip()


def wake_variants(wake_word: str, aliases: List[str]) -> List[str]:
    out = []
    for item in [wake_word, *(aliases or [])]:
        n = normalize_text(item)
        if n and n not in out:
            out.append(n)
    return out


def _phrase_regex(phrase: str) -> re.Pattern:
    parts = [re.escape(p) for p in phrase.split() if p]
    if not parts:
        return re.compile(r"^$")
    return re.compile(r"\b" + r"\W+".join(parts) + r"\b", re.IGNORECASE)


def contains_wake_word(transcript: str, wake_word: str, aliases: List[str]) -> Dict[str, object]:
    raw = str(transcript or "")
    if not raw.strip():
        return {"detected": False, "score": None, "matchedPhrase": None}

    variants = wake_variants(wake_word, aliases)
    for phrase in variants:
        re_phrase = _phrase_regex(phrase)
        if re_phrase.search(raw):
            score = 0.95 if phrase == normalize_text(wake_word) else 0.82
            return {"detected": True, "score": score, "matchedPhrase": phrase}

    # Soft fallback: plain single token "andromeda" substring with boundaries.
    core = normalize_text(wake_word).split(" ")[-1] if normalize_text(wake_word) else "andromeda"
    if core:
        soft = re.compile(rf"\b{re.escape(core)}\b", re.IGNORECASE)
        if soft.search(raw):
            return {"detected": True, "score": 0.70, "matchedPhrase": core}

    # Fuzzy fallback for STT misspellings (e.g., "adromeda", "andromida").
    # Keep conservative threshold to reduce false positives.
    norm_raw = normalize_text(raw)
    raw_tokens = [t for t in norm_raw.split(" ") if t]
    if raw_tokens and variants:
        max_score = 0.0
        best_phrase = None

        def _compact(s: str) -> str:
            return re.sub(r"\s+", "", normalize_text(s))

        compact_variants = [(_compact(v), v) for v in variants if _compact(v)]

        for compact_var, phrase in compact_variants:
            token_windows: List[str] = []
            # compare token windows up to length 3 against wake variants
            for width in (1, 2, 3):
                if len(raw_tokens) < width:
                    continue
                for i in range(0, len(raw_tokens) - width + 1):
                    token_windows.append("".join(raw_tokens[i:i + width]))

            if not token_windows:
                continue

            for candidate in token_windows:
                score = SequenceMatcher(None, compact_var, candidate).ratio()
                if score > max_score:
                    max_score = score
                    best_phrase = phrase

        if max_score >= 0.82 and best_phrase:
            return {"detected": True, "score": round(max_score, 3), "matchedPhrase": f"fuzzy:{best_phrase}"}

    return {"detected": False, "score": None, "matchedPhrase": None}


def strip_wake_word(transcript: str, wake_word: str, aliases: List[str]) -> str:
    raw = str(transcript or "").strip()
    if not raw:
        return ""

    variants = wake_variants(wake_word, aliases)
    out = raw
    for phrase in variants:
        re_phrase = _phrase_regex(phrase)
        if re_phrase.search(out):
            out = re_phrase.sub("", out, count=1)
            break

    out = re.sub(r"^[\s,.:;\-–—!?]+", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out
