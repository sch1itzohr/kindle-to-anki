#!/usr/bin/env python3
"""Import Kindle Vocabulary Builder words into Anki through AnkiConnect."""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sqlite3
import sys
import tempfile
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_ANKI_URL = "http://localhost:8765"
DEFAULT_MODEL = "Kindle Vocabulary"
DEFAULT_CHECKPOINT = Path.home() / ".kindle-to-anki"


@dataclass(frozen=True)
class Lookup:
    word: str
    context: str
    timestamp: int
    word_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Enrichment:
    word: str
    part_of_speech: str = ""
    pronunciation: str = ""
    definition: str = ""
    example: str = ""
    audio_url: str = ""


def normalize_word(word: str) -> str:
    return " ".join(clean_word(word).split()).casefold()


def clean_word(word: str) -> str:
    """Remove Kindle's invisible markers while retaining accents."""
    return (unicodedata.normalize("NFC", word)
            .replace("\u200b", "")
            .replace("\ufeff", "")
            .replace("\u00ad", "")
            .strip())


def read_checkpoint(path: Path) -> int:
    try:
        return int(path.read_text(encoding="utf-8").strip() or "0")
    except (FileNotFoundError, ValueError):
        return 0


def write_checkpoint(path: Path, timestamp: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(str(timestamp))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def load_lookups(database: Path, since: int = 0) -> list[Lookup]:
    """Load distinct words and all their contexts from a Kindle vocab.db."""
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            """
            SELECT w.id, w.stem, COALESCE(l.usage, ''), COALESCE(w.timestamp, 0)
            FROM WORDS AS w
            LEFT JOIN LOOKUPS AS l ON w.id = l.word_key
            WHERE w.timestamp > ?
            ORDER BY w.timestamp, w.stem
            """,
            (since,),
        ).fetchall()
    finally:
        connection.close()

    grouped: dict[str, Lookup] = {}
    contexts: dict[str, list[str]] = {}
    ids: dict[str, list[str]] = {}
    for word_id, word, context, timestamp in rows:
        key = normalize_word(word)
        if not key:
            continue
        ids.setdefault(key, [])
        if str(word_id) not in ids[key]:
            ids[key].append(str(word_id))
        contexts.setdefault(key, [])
        if context and context not in contexts[key]:
            contexts[key].append(context)
        previous = grouped.get(key)
        if previous is None or timestamp > previous.timestamp:
            display_word = previous.word if previous is not None else clean_word(word)
            grouped[key] = Lookup(display_word, "", int(timestamp), tuple(ids[key]))

    return [
        Lookup(item.word, "<br>".join(contexts[key]), item.timestamp, item.word_ids)
        for key, item in sorted(grouped.items(), key=lambda pair: pair[1].timestamp)
    ]


def delete_failed_words(database: Path, lookups: Iterable[Lookup]) -> int:
    """Delete only the exact WORDS rows represented by failed lookups."""
    word_ids = sorted({word_id for lookup in lookups for word_id in lookup.word_ids})
    if not word_ids:
        return 0
    placeholders = ",".join("?" for _ in word_ids)
    connection = sqlite3.connect(database)
    try:
        connection.execute("BEGIN")
        connection.execute(f"DELETE FROM LOOKUPS WHERE word_key IN ({placeholders})", word_ids)
        result = connection.execute(f"DELETE FROM WORDS WHERE id IN ({placeholders})", word_ids)
        connection.commit()
        return result.rowcount
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _strip_markup(value: str) -> str:
    value = re.sub(r"\{/?(?:bc|it|b|inf|sup|ldquo|rdquo|a_link|sx)(?:\|[^}]*)?\}", "", value)
    value = value.replace("{ldquo}", "&ldquo;").replace("{rdquo}", "&rdquo;")
    return value.strip()


def _html_text(value: str) -> str:
    value = _strip_markup(value)
    value = html.escape(value, quote=False)
    return value.replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")


def _first_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            result = _first_text(item)
            if result:
                return result
    if isinstance(value, dict):
        for key in ("text", "t", "dt"):
            if key in value:
                result = _first_text(value[key])
                if result:
                    return result
    return ""


def _find_texts(value: Any, key: str) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        if key in value:
            candidate = value[key]
            if isinstance(candidate, list):
                found.extend(x for x in candidate if isinstance(x, str))
            elif isinstance(candidate, str):
                found.append(candidate)
        for child in value.values():
            found.extend(_find_texts(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_find_texts(child, key))
    return found


class MerriamWebster:
    endpoint = "https://www.dictionaryapi.com/api/v3/references/collegiate/json/"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self.api_key = api_key
        self.timeout = timeout

    class LookupError(LookupError):
        def __init__(self, word: str, detail: str, *, url: str, status: int | None = None, response: str = ""):
            self.word = word
            self.detail = detail
            self.url = url
            self.status = status
            self.response = response
            super().__init__(self.__str__())

        def __str__(self) -> str:
            status = f" HTTP {self.status}." if self.status is not None else ""
            response = f" Response: {self.response}" if self.response else ""
            return f"no dictionary entry for {self.word!r}.{status} {self.detail}.{response} URL: {self.url}"

    @staticmethod
    def _diagnostic_url(word: str) -> str:
        return MerriamWebster.endpoint + urllib.parse.quote(word, safe="") + "?key=<redacted>"

    def lookup(self, word: str) -> Enrichment:
        word = clean_word(word)
        if not word:
            raise self.LookupError(word, "the Kindle word is empty after removing invisible characters", url=self._diagnostic_url(word))
        query = urllib.parse.quote(word, safe="")
        url = f"{self.endpoint}{query}?key={urllib.parse.quote(self.api_key)}"
        diagnostic_url = self._diagnostic_url(word)
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = getattr(response, "status", None)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            raise self.LookupError(word, "the dictionary API rejected the request", url=diagnostic_url, status=exc.code, response=raw[:500]) from exc
        except urllib.error.URLError as exc:
            raise self.LookupError(word, f"the dictionary API could not be reached: {exc.reason}", url=diagnostic_url) from exc
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise self.LookupError(word, "the dictionary API returned invalid JSON", url=diagnostic_url, status=status, response=raw[:500]) from exc

        entries = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        entry = next((item for item in entries if item.get("shortdef") or item.get("def")), None)
        if entry is None:
            suggestions = [str(item) for item in data if isinstance(item, str)] if isinstance(data, list) else []
            detail = "the API returned no dictionary object"
            if suggestions:
                detail += f"; suggestions: {', '.join(suggestions[:10])}"
            elif isinstance(data, list):
                detail += f"; returned list length: {len(data)}"
            else:
                detail += f"; returned JSON type: {type(data).__name__}"
            raise self.LookupError(word, detail, url=diagnostic_url, status=status, response=raw[:500])
        pronunciation = ""
        pronunciations = entry.get("hwi", {}).get("prs", [])
        if isinstance(pronunciations, list) and pronunciations:
            first_pronunciation = pronunciations[0]
            if isinstance(first_pronunciation, dict):
                pronunciation = str(first_pronunciation.get("mw", ""))
        audio = ""
        for pronunciation_item in entry.get("hwi", {}).get("prs", []):
            if isinstance(pronunciation_item, dict):
                sound = pronunciation_item.get("sound", {})
                if isinstance(sound, dict) and sound.get("audio"):
                    audio = str(sound["audio"])
                    break
        if audio:
            if audio.startswith(("bix", "gg")):
                directory = audio[:3]
            elif audio[0].isdigit() or not audio[0].isalpha():
                directory = "number"
            else:
                directory = audio[0]
            audio_url = f"https://media.merriam-webster.com/audio/prons/en/us/mp3/{directory}/{audio}.mp3"
        else:
            audio_url = ""

        definitions = entry.get("shortdef", [])
        definition = "<br>".join(_html_text(x) for x in definitions[:3] if isinstance(x, str))
        examples = [_html_text(x) for x in _find_texts(entry, "t") if x.strip()]
        return Enrichment(
            word=word,
            part_of_speech=str(entry.get("fl", "")),
            pronunciation=pronunciation,
            definition=definition,
            example=examples[0] if examples else "",
            audio_url=audio_url,
        )


class AnkiConnect:
    def __init__(self, url: str = DEFAULT_ANKI_URL, timeout: float = 15.0):
        self.url = url
        self.timeout = timeout

    def invoke(self, action: str, **params: Any) -> Any:
        payload = json.dumps({"action": action, "version": 5, "params": params}).encode()
        request = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ConnectionError(f"cannot connect to AnkiConnect at {self.url}: {exc}") from exc
        if result.get("error"):
            raise RuntimeError(f"AnkiConnect {action} failed: {result['error']}")
        return result.get("result")

    def ensure_deck(self, deck: str) -> None:
        self.invoke("createDeck", deck=deck)
        if deck not in self.invoke("deckNames"):
            raise RuntimeError(f"Anki did not create or expose target deck {deck!r}")

    def ensure_model(self, model: str) -> None:
        fields = ["Word", "PartOfSpeech", "Pronunciation", "Audio", "Definition", "Example", "KindleContext"]
        if model in self.invoke("modelNames"):
            existing = self.invoke("modelFieldNames", modelName=model)
            if existing != fields:
                raise RuntimeError(f"Anki model {model!r} exists with incompatible fields: {existing}")
            return
        self.invoke(
            "createModel",
            modelName=model,
            inOrderFields=fields,
            css=""".card { font-family: Arial; font-size: 20px; text-align: center; } .word { font-size: 28px; font-weight: bold; } .context { color: #666; font-size: 16px; }""",
            isCloze=False,
            cardTemplates=[
                {
                    "Name": "Kindle word",
                    "Front": "<div class=word>{{Word}}</div><div>{{PartOfSpeech}}</div>",
                    "Back": "{{FrontSide}}<hr>{{Pronunciation}}<br>{{Audio}}<div>{{Definition}}</div>{{#Example}}<hr><div>{{Example}}</div>{{/Example}}{{#KindleContext}}<hr><div class=context>{{KindleContext}}</div>{{/KindleContext}}",
                }
            ],
        )

    def add_notes(self, deck: str, model: str, notes: Iterable[tuple[Lookup, Enrichment]]) -> list[Any]:
        payload = []
        for lookup, enrichment in notes:
            fields = {
                "Word": enrichment.word,
                "PartOfSpeech": enrichment.part_of_speech,
                "Pronunciation": enrichment.pronunciation,
                "Audio": "[sound:%s.mp3]" % enrichment.audio_url.rsplit("/", 1)[-1] if enrichment.audio_url else "",
                "Definition": enrichment.definition,
                "Example": enrichment.example,
                "KindleContext": lookup.context,
            }
            note: dict[str, Any] = {
                "deckName": deck,
                "modelName": model,
                "fields": fields,
                "tags": ["kindle"],
                "options": {"allowDuplicate": False, "duplicateScope": "deck"},
            }
            if enrichment.audio_url:
                note["audio"] = {"url": enrichment.audio_url, "filename": enrichment.audio_url.rsplit("/", 1)[-1], "fields": ["Audio"]}
            payload.append(note)
        return self.invoke("addNotes", notes=payload) if payload else []

    def move_notes_to_deck(self, deck: str, notes: Iterable[tuple[Lookup, Enrichment]], note_ids: Iterable[Any]) -> None:
        """Enforce the target deck, including notes created by a previous retry."""
        ids_by_word = {enrichment.word: note_id for (_, enrichment), note_id in zip(notes, note_ids)}
        all_card_ids: list[Any] = []
        for word, note_id in ids_by_word.items():
            note_ids_for_word = [note_id] if note_id else []
            if not note_ids_for_word:
                escaped = word.replace('\\', '\\\\').replace('"', '\\"')
                note_ids_for_word = self.invoke("findNotes", query=f'Word:"{escaped}"') or []
            for note_id in note_ids_for_word:
                cards = self.invoke("findCards", query=f"nid:{note_id}") or []
                all_card_ids.extend(cards)
        if all_card_ids:
            self.invoke("changeDeck", cards=all_card_ids, deck=deck)

    def move_tagged_notes_to_deck(self, deck: str, tag: str = "kindle") -> int:
        """Repair cards from earlier runs that were placed in the wrong deck."""
        cards = self.invoke("findCards", query=f"tag:{tag}") or []
        if cards:
            self.invoke("changeDeck", cards=cards, deck=deck)
        return len(cards)


def run(args: argparse.Namespace) -> int:
    if args.dry_run and args.delete_failed:
        print("ERROR: --delete-failed cannot be used with --dry-run.", file=sys.stderr)
        return 2
    checkpoint = Path(args.checkpoint).expanduser()
    since = 0 if args.all else read_checkpoint(checkpoint)
    lookups = load_lookups(Path(args.kindle_db).expanduser(), since)
    if not lookups:
        print("No new Kindle words found.")
        return 0

    provider = MerriamWebster(args.mw_api_key, args.timeout)
    enriched: list[tuple[Lookup, Enrichment]] = []
    failures: list[tuple[Lookup, Exception]] = []
    for index, lookup in enumerate(lookups, 1):
        print(f"[{index}/{len(lookups)}] Looking up {lookup.word}...", flush=True)
        try:
            enriched.append((lookup, provider.lookup(lookup.word)))
        except (LookupError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as exc:
            failures.append((lookup, exc))

    if failures:
        print(f"Failed lookups ({len(failures)}):", file=sys.stderr)
        for lookup, failure in failures:
            print(f"ERROR {lookup.word!r}: {failure}", file=sys.stderr)
        if not args.delete_failed:
            print("Checkpoint was not advanced. Use --delete-failed only if you want these Kindle rows removed.", file=sys.stderr)
            return 1
        deleted = delete_failed_words(Path(args.kindle_db).expanduser(), (lookup for lookup, _ in failures))
        print(f"Deleted {deleted} failed word row(s) from {args.kindle_db}.", file=sys.stderr)
    if args.dry_run:
        for lookup, item in enriched:
            print(f"{item.word}: {item.definition or '(no definition)'}")
        return 0

    if not enriched:
        write_checkpoint(checkpoint, max(item.timestamp for item in lookups))
        print("No dictionary lookups succeeded; failed rows were removed.")
        return 0
    if args.batch_size < 1:
        print("ERROR: --batch-size must be at least 1.", file=sys.stderr)
        return 2

    anki = AnkiConnect(args.anki_url, args.timeout)
    anki.ensure_deck(args.deck)
    anki.ensure_model(args.model)
    repaired = anki.move_tagged_notes_to_deck(args.deck)
    if repaired:
        print(f"Moved {repaired} existing Kindle card(s) to {args.deck!r}.", flush=True)
    added = 0
    duplicates = 0
    for start in range(0, len(enriched), args.batch_size):
        batch = enriched[start:start + args.batch_size]
        batch_number = start // args.batch_size + 1
        total_batches = (len(enriched) + args.batch_size - 1) // args.batch_size
        print(f"Adding Anki batch {batch_number}/{total_batches} ({len(batch)} cards)...", flush=True)
        results = anki.add_notes(args.deck, args.model, batch)
        added += sum(result is not None for result in results)
        duplicates += len(results) - sum(result is not None for result in results)
        anki.move_notes_to_deck(args.deck, batch, results)
        print(f"Finished Anki batch {batch_number}/{total_batches}.", flush=True)
    write_checkpoint(checkpoint, max(item.timestamp for item in lookups))
    print(f"Imported {added} words; skipped {duplicates} duplicates.")
    if failures:
        print(f"Removed {len(failures)} failed lookup(s) because --delete-failed was provided.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kindle-db", required=True, help="Path to Kindle vocab.db")
    parser.add_argument("--deck", required=True, help="Target Anki deck name")
    parser.add_argument("--mw-api-key", default=os.environ.get("MW_API_KEY"), help="Merriam-Webster API key (or MW_API_KEY)")
    parser.add_argument("--anki-url", default=DEFAULT_ANKI_URL)
    parser.add_argument("--checkpoint", default=str(DEFAULT_CHECKPOINT))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--batch-size", type=int, default=10, help="Number of cards sent to Anki per request (default: 10)")
    parser.add_argument("--all", action="store_true", help="Process all Kindle words, ignoring the checkpoint")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and display cards without contacting Anki")
    parser.add_argument("--delete-failed", action="store_true", help="Delete failed lookup words from vocab.db after listing them")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.mw_api_key:
        parser.error("--mw-api-key or MW_API_KEY is required")
    try:
        return run(args)
    except (OSError, sqlite3.Error, ConnectionError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
