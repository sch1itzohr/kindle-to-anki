import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import kindle_to_anki as app


class KindleDatabaseTests(unittest.TestCase):
    def test_groups_contexts_and_filters_by_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vocab.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE WORDS (id TEXT PRIMARY KEY, stem TEXT, timestamp INTEGER);
                CREATE TABLE LOOKUPS (word_key TEXT, usage TEXT);
                INSERT INTO WORDS VALUES ('1', 'Serendipity', 10);
                INSERT INTO WORDS VALUES ('2', 'serendipity', 12);
                INSERT INTO WORDS VALUES ('3', 'old', 5);
                INSERT INTO LOOKUPS VALUES ('1', 'A serendipitous discovery.');
                INSERT INTO LOOKUPS VALUES ('2', 'It happened by serendipity.');
            """)
            connection.commit()
            connection.close()

            result = app.load_lookups(database, since=9)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].word, "Serendipity")
            self.assertIn("serendipitous", result[0].context)
            self.assertEqual(result[0].timestamp, 12)

    def test_deletes_exact_failed_rows_and_contexts(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "vocab.db"
            connection = sqlite3.connect(database)
            connection.executescript("""
                CREATE TABLE WORDS (id TEXT PRIMARY KEY, stem TEXT, timestamp INTEGER);
                CREATE TABLE LOOKUPS (word_key TEXT, usage TEXT);
                INSERT INTO WORDS VALUES ('bad', 'missing', 10);
                INSERT INTO WORDS VALUES ('good', 'known', 11);
                INSERT INTO LOOKUPS VALUES ('bad', 'missing context');
                INSERT INTO LOOKUPS VALUES ('good', 'known context');
            """)
            connection.commit()
            connection.close()

            failed = app.load_lookups(database, since=0)[:1]
            self.assertEqual(app.delete_failed_words(database, failed), 1)
            connection = sqlite3.connect(database)
            self.assertEqual(connection.execute("SELECT id FROM WORDS ORDER BY id").fetchall(), [("good",)])
            self.assertEqual(connection.execute("SELECT word_key FROM LOOKUPS").fetchall(), [("good",)])
            connection.close()


class DictionaryTests(unittest.TestCase):
    def test_maps_definition_example_and_audio(self):
        body = [{
            "fl": "noun",
            "hwi": {"prs": [{"mw": "ˌsərənˈdipətē", "sound": {"audio": "serend01"}}]},
            "shortdef": ["the occurrence of events by chance"],
            "def": [{"sseq": [["sense", {"dt": [["text", "{bc}a useful example"]]}]]}],
        }]
        response = type("Response", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
            "read": lambda self: json.dumps(body).encode(),
        })()
        with patch("urllib.request.urlopen", return_value=response):
            result = app.MerriamWebster("key").lookup("serendipity")
        self.assertEqual(result.part_of_speech, "noun")
        self.assertEqual(result.pronunciation, "ˌsərənˈdipətē")
        self.assertIn("occurrence", result.definition)
        self.assertIn("serend01.mp3", result.audio_url)

    def test_surfaces_suggestions_when_api_returns_no_entry(self):
        response = type("Response", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
            "read": lambda self: json.dumps(["jook", "jooks"]).encode(),
            "status": 200,
        })()
        with patch("urllib.request.urlopen", return_value=response):
            with self.assertRaises(app.MerriamWebster.LookupError) as raised:
                app.MerriamWebster("key").lookup("jook")
        message = str(raised.exception)
        self.assertIn("suggestions: jook, jooks", message)
        self.assertIn("key=<redacted>", message)

    def test_removes_invisible_word_markers(self):
        self.assertEqual(app.clean_word("\ufeffblasé\u200b"), "blasé")


class AnkiTests(unittest.TestCase):
    def test_invoke_sends_expected_action(self):
        response = type("Response", (), {
            "__enter__": lambda self: self,
            "__exit__": lambda self, *args: None,
            "read": lambda self: b'{"result": ["Basic"], "error": null}',
        })()
        with patch("urllib.request.urlopen", return_value=response) as request:
            result = app.AnkiConnect().invoke("modelNames")
        self.assertEqual(result, ["Basic"])
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload["action"], "modelNames")
        self.assertEqual(payload["version"], 5)


if __name__ == "__main__":
    unittest.main()
