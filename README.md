# Bundlecheck

**An offline translation-catalog checker for plain `key = value` locale files.**

Bundlecheck compares every target locale against your source catalog and tells you what is missing, what is broken, and how far along each translation is. It works on one readable text file per locale, needs only the Python 3 standard library, and gates CI with exit codes.

## Features

- **Plain-text catalogs.** `key = value` lines, `#` comments, blank lines, and quoted values with `\n`, `\t`, `\"` and `\\` escapes.
- **Line-numbered errors.** Every problem points at `file:line`, so you can fix it in your editor.
- **Coverage per locale.** Translated keys as a percentage of the source, rounded half-up to one decimal place.
- **Missing and extra keys.** Keys absent from a target, and keys a target has that the source does not.
- **Empty translations.** Keys defined with no value.
- **Duplicate keys.** Both line numbers are reported, and the first definition wins.
- **Syntax and key checks.** Lines without `=`, unterminated quotes, and keys with characters outside `A-Z a-z 0-9 _ . -`.
- **Placeholder matching.** Compares `{name}`, `%s`, `%d`, `%1$s` and similar placeholders as multisets. Reordering is fine; a missing or invented placeholder is an error. `%%` is treated as a literal percent sign.
- **Untranslated detection.** Flags values identical to the source, with an `--allow-same KEY` escape hatch for words like brand names.
- **Length-ratio warnings.** Flags translations much longer or shorter than the source, which often means truncated or padded UI text. Bounds are adjustable.
- **Coverage gate.** `--min-coverage 90` turns a locale below the threshold into an error.
- **Stub generation.** `stubs` writes `# TODO: translate` entries for every missing key, with the source text copied in and escaped correctly.
- **Standalone lint.** `lint` checks any catalog files on their own, without a source.
- **CI-friendly exit codes.** `0` pass, `1` gate failed, `2` unreadable input or bad options. `--fail-on error|warning|never` sets the threshold.
- **JSON report.** `--json` prints a machine-readable report for dashboards or other tools.
- **Several locales at once.** Pass as many target files as you like to a single `check` run.

## Quick start

Given a source catalog and a French file (both in `examples/`):

```text
examples/en.txt
  app.title = "Field Notes"
  msg.uploading = Uploading %d files
  ...

examples/fr.txt
  msg.uploading = Envoi de %d fichiers
  ...
```

Check the French file against the source:

```sh
python3 bundlecheck.py check examples/en.txt examples/fr.txt
```

Check several locales and gate CI at 90% coverage:

```sh
python3 bundlecheck.py check examples/en.txt examples/fr.txt examples/de.txt --min-coverage 90
```

Generate stubs for the keys a locale is missing. `-o` writes a separate file (and overwrites it if it exists); paste its contents into your locale file, then translate the stubbed values:

```sh
python3 bundlecheck.py stubs examples/en.txt examples/fr.txt -o fr-stubs.txt
```

Lint a file on its own, for example to find the duplicate and syntax errors in the German sample:

```sh
python3 bundlecheck.py lint examples/de.txt
```

## Requirements

- Python 3.10 or newer (the code uses `X | None` annotations). Nothing else; no third-party packages.

## How to run

Run the module directly from the project directory:

```sh
python3 bundlecheck.py check SOURCE TARGET [TARGET ...] [options]
python3 bundlecheck.py stubs SOURCE TARGET [-o FILE]
python3 bundlecheck.py lint FILE [FILE ...] [--fail-on LEVEL]
```

`check` options:

| Option | Meaning |
| --- | --- |
| `--fail-on {error,warning,never}` | Lowest level that makes the exit code `1` (default `error`) |
| `--min-coverage PCT` | Minimum translated percentage per locale |
| `--min-ratio X` / `--max-ratio X` | Acceptable target/source length ratio (defaults `0.5` and `1.8`) |
| `--allow-same KEY` | Key that may stay identical to the source; repeat for several |
| `--json` | Print the report as JSON |

## File format

```text
# comments start with # at the beginning of a line
app.title = "Field Notes"
app.greeting = Hello, {name}!
msg.uploading = Uploading %d files
```

Only lines starting with `#` are comments; a `#` later on a line is part of the value. Quote a value (`"..."`) to keep leading or trailing spaces, or to use `\n`, `\t`, `\"` and `\\` escapes. Keys are case-sensitive.

## How to test

From the project directory:

```sh
python3 -m unittest discover -s tests -v
```

The tests also run under pytest (`python3 -m pytest`) if you have it installed. They cover parsing and escapes, placeholder rules, coverage rounding, the stub round trip, every CLI exit code, and the JSON report. They use temporary directories and never touch the network.

## Project layout

```text
bundlecheck.py        the whole tool: parser, checks, reports and CLI
examples/             sample en / fr / de catalogs, with deliberate mistakes in fr and de
tests/                unittest suite (runs under pytest too)
```

## Limitations

- Only the `key = value` format is read. JSON, YAML and `.po` files are not supported.
- Length ratios are character counts, not display widths, so they say nothing about CJK or right-to-left layout.
- Placeholders recognised: `{name}` style braces (no nesting), and printf-style `%s`, `%d`, `%f`, `%i` and positional `%1$s`. Anything else, such as `%.2f` or `%ld`, is not recognised. ICU plural blocks (`{n, plural, ...}`) are not parsed: only their innermost `{...}` pieces are compared, so a translation whose plural forms differ from the source can be reported as a placeholder mismatch.
- Quoted values are strict: an unescaped `"` inside a quoted value is a syntax error, not silently accepted, so write `\"` for a literal quote.
- A file with a UTF-8 byte-order mark is accepted.
