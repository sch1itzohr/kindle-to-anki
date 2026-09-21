# Kindle-to-Anki

The recommended workflow is now the direct AnkiConnect importer:

```bash
export MW_API_KEY=your-merriam-webster-key
python3 kindle_to_anki.py --kindle-db /path/to/vocab.db --deck "Kindle Words"
```

Anki Desktop must be running with the AnkiConnect add-on enabled. The importer
creates a `Kindle Vocabulary` note type, adds definitions/examples/audio, keeps
Kindle lookup context on the card, and remembers the last processed timestamp
in `~/.kindle-to-anki`. Use `--dry-run` to inspect cards without contacting
Anki, or `--all` to process the complete database.

If a word cannot be found, the error includes the API status, returned
suggestions or response preview, and the exact URL with the API key redacted.
Use `--all --dry-run` to retry and inspect dictionary failures without adding
anything to Anki.

Failed words are always printed. To permanently remove only those failed words
from `vocab.db`, add the explicit `--delete-failed` flag:

```bash
python3 kindle_to_anki.py --kindle-db /path/to/vocab.db \
  --deck "Kindle Words" --delete-failed
```

This deletes the related `WORDS` and `LOOKUPS` rows in a SQLite transaction and
cannot be combined with `--dry-run`.

Anki imports are sent in batches of 10 by default. Adjust this with
`--batch-size 5` or `--batch-size 25`. Existing cards tagged `kindle` are also
moved into the requested deck on each run, repairing cards from older runs that
landed in `Default`.

Import from kindle vocabulary to anki-ready csv file. Uses LingvoLeo service to
get translataion, transcription, pronounciation and some image.

Find all lookups (words you've been looking for while reading Kindle) and contexts. Retrieve translations from LinguaLeo, export to `csv` file.

Take first three translations for each word.

Written in Python3.

# Usage

```
usage: export.py [-h] [--kindle KINDLE] [--src SRC] [-m MEDIA_PATH] [-o OUT]
                 [-s SKIP]
                 email pwd

positional arguments:
  email                 LinguaLeo account email/login
  pwd                   LinguaLeo account password

optional arguments:
  -h, --help            show this help message and exit
  --kindle KINDLE       Path to kindle db file (usually vocab.db)
  --src SRC             Path to plain text file with newline separated list of
                        words
  -m MEDIA_PATH, --media-path MEDIA_PATH
                        Where to store media files (sounds/images)
  -o OUT, --out OUT     Output filename
```

Use `--kindle` switch to export from recent Kindle lookups. Use `--src` switch to export from
plain text file.

It should be formatted as follows:

    word1 context1
    word2 context2
    ...
    wordN contextN

Word and context are splitted by space. So, first occurence is word and remaining is context.

When using Kindle as input last timestamp is written to `~/.kindle`. During next import only new lookups are exported. One can manipulate value written to `~/.kindle` to get only needed words from Kindle.
