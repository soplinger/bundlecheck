"""Offline translation-catalog checker for plain key = value locale files.

Compares each target locale against a source catalog and reports missing,
extra, empty, duplicate and untranslated keys, placeholder mismatches,
length-ratio outliers and coverage. Stub files for missing keys can be
generated, and every problem carries a file:line location where possible.
"""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
PLACEHOLDER_RE = re.compile(r"\{[^{}]*\}|%(?:\d+\$)?[sdfi]")

FAIL_LEVELS = {
    "error": {"error"},
    "warning": {"error", "warning"},
    "never": set(),
}

ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


@dataclass
class Issue:
    level: str
    code: str
    locale: str
    key: str | None
    line: int | None
    message: str


@dataclass
class Entry:
    value: str
    line: int


@dataclass
class Catalog:
    name: str
    entries: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)


@dataclass
class Options:
    min_coverage: Decimal = Decimal("0")
    min_ratio: float = 0.5
    max_ratio: float = 1.8
    allow_same: frozenset = frozenset()


@dataclass
class LocaleReport:
    locale: str
    total: int
    covered: int
    coverage: Decimal
    issues: list


@dataclass
class Result:
    source: Catalog
    source_issues: list
    reports: list

    @property
    def all_issues(self):
        out = list(self.source_issues)
        for rep in self.reports:
            out.extend(rep.issues)
        return out

    def passed(self, fail_on):
        return gate(self.all_issues, fail_on)


def gate(issues, fail_on):
    failing = FAIL_LEVELS[fail_on]
    return not any(issue.level in failing for issue in issues)


def _unquote(raw):
    """Return the unescaped body of a quoted value, or None if it is malformed."""
    body = raw[1:-1]
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            if i + 1 >= len(body):
                return None
            out.append(ESCAPES.get(body[i + 1], "\\" + body[i + 1]))
            i += 2
        elif ch == '"':
            return None
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def serialize_value(value):
    """Quote a value only when it would not survive a bare round trip."""
    needs_quotes = (
        value == ""
        or value != value.strip()
        or any(ch in value for ch in '\n\t"\\')
    )
    if not needs_quotes:
        return value
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def parse_text(text, name="catalog"):
    cat = Catalog(name=name)
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            cat.issues.append(
                Issue("error", "syntax", name, None, lineno, "expected key = value")
            )
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not KEY_RE.match(key):
            cat.issues.append(
                Issue("error", "bad-key", name, key, lineno, f"invalid key '{key}'")
            )
            continue
        if value.startswith('"'):
            if len(value) < 2 or not value.endswith('"'):
                cat.issues.append(
                    Issue("error", "syntax", name, key, lineno, "unterminated quoted value")
                )
                continue
            unquoted = _unquote(value)
            if unquoted is None:
                cat.issues.append(
                    Issue("error", "syntax", name, key, lineno,
                          "malformed quoted value (unescaped quote or trailing backslash)")
                )
                continue
            value = unquoted
        if key in cat.entries:
            first = cat.entries[key].line
            cat.issues.append(
                Issue(
                    "error",
                    "duplicate",
                    name,
                    key,
                    lineno,
                    f"key '{key}' already defined on line {first}",
                )
            )
            continue
        cat.entries[key] = Entry(value, lineno)
    return cat


def load_catalog(path):
    text = Path(path).read_text(encoding="utf-8-sig")
    return parse_text(text, name=str(path))


def _placeholders(text):
    return sorted(PLACEHOLDER_RE.findall(text.replace("%%", "")))


def placeholder_problem(source_value, target_value):
    want = Counter(_placeholders(source_value))
    got = Counter(_placeholders(target_value))
    missing = sorted((want - got).elements())
    unexpected = sorted((got - want).elements())
    if not missing and not unexpected:
        return None
    parts = []
    if missing:
        parts.append("missing " + ", ".join(missing))
    if unexpected:
        parts.append("unexpected " + ", ".join(unexpected))
    return "placeholders differ: " + "; ".join(parts)


def check_locale(source, target, opts):
    issues = list(target.issues)
    total = len(source.entries)
    covered = 0
    for key, src_entry in source.entries.items():
        src = src_entry.value
        ent = target.entries.get(key)
        if ent is None:
            issues.append(
                Issue("error", "missing", target.name, key, None, f"missing key '{key}'")
            )
            continue
        val = ent.value
        if val == "":
            issues.append(
                Issue("error", "empty", target.name, key, ent.line, f"empty translation for '{key}'")
            )
            continue
        problem = placeholder_problem(src, val)
        if problem:
            issues.append(
                Issue("error", "placeholder", target.name, key, ent.line, f"'{key}': {problem}")
            )
        identical = src != "" and val == src and key not in opts.allow_same
        if identical:
            issues.append(
                Issue(
                    "warning",
                    "untranslated",
                    target.name,
                    key,
                    ent.line,
                    f"'{key}' is identical to the source",
                )
            )
        else:
            covered += 1
        if src != "":
            ratio = len(val) / len(src)
            if ratio < opts.min_ratio or ratio > opts.max_ratio:
                issues.append(
                    Issue(
                        "warning",
                        "length",
                        target.name,
                        key,
                        ent.line,
                        f"'{key}' length ratio {ratio:.2f} outside "
                        f"{opts.min_ratio:.2f}-{opts.max_ratio:.2f}",
                    )
                )
    for key, ent in target.entries.items():
        if key not in source.entries:
            issues.append(
                Issue(
                    "warning",
                    "extra",
                    target.name,
                    key,
                    ent.line,
                    f"key '{key}' is not in the source",
                )
            )
    if total == 0:
        pct = Decimal(100)
    else:
        pct = (Decimal(covered) * 100 / Decimal(total)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    if pct < opts.min_coverage:
        issues.append(
            Issue(
                "error",
                "coverage",
                target.name,
                None,
                None,
                f"coverage {pct}% is below the {opts.min_coverage}% minimum",
            )
        )
    return LocaleReport(target.name, total, covered, pct, issues)


def empty_source_issues(source):
    return [
        Issue("warning", "empty", source.name, key, ent.line, f"source value for '{key}' is empty")
        for key, ent in source.entries.items()
        if ent.value == ""
    ]


def run_check(source, targets, opts):
    source_issues = list(source.issues) + empty_source_issues(source)
    reports = [check_locale(source, target, opts) for target in targets]
    return Result(source=source, source_issues=source_issues, reports=reports)


def stub_text(source, target):
    lines = []
    count = 0
    for key, ent in source.entries.items():
        if key in target.entries:
            continue
        lines.append("# TODO: translate")
        lines.append(f"{key} = {serialize_value(ent.value)}")
        count += 1
    text = "\n".join(lines) + "\n" if lines else ""
    return text, count


def _fmt(issue):
    loc = issue.locale
    if issue.line:
        loc = f"{loc}:{issue.line}"
    return f"  {issue.level.upper():<8}{loc:<28}{issue.code:<13}{issue.message}"


def _summary(issues, passed):
    errors = sum(1 for i in issues if i.level == "error")
    warnings = sum(1 for i in issues if i.level == "warning")
    verdict = "PASS" if passed else "FAIL"
    return f"{errors} error(s), {warnings} warning(s) -> {verdict}"


def render_text(result, fail_on):
    src = result.source
    lines = [f"source {src.name}: {len(src.entries)} key(s)"]
    lines += [_fmt(i) for i in result.source_issues]
    for rep in result.reports:
        lines.append("")
        lines.append(f"locale {rep.locale}: {rep.covered}/{rep.total} translated ({rep.coverage}%)")
        if not rep.issues:
            lines.append("  no issues")
        lines += [_fmt(i) for i in rep.issues]
    lines.append("")
    lines.append(_summary(result.all_issues, result.passed(fail_on)))
    return "\n".join(lines)


def render_json(result, fail_on):
    def issue_dict(issue):
        return {
            "level": issue.level,
            "code": issue.code,
            "locale": issue.locale,
            "key": issue.key,
            "line": issue.line,
            "message": issue.message,
        }

    payload = {
        "source": result.source.name,
        "keys": len(result.source.entries),
        "source_issues": [issue_dict(i) for i in result.source_issues],
        "locales": [
            {
                "locale": rep.locale,
                "total": rep.total,
                "covered": rep.covered,
                "coverage": str(rep.coverage),
                "issues": [issue_dict(i) for i in rep.issues],
            }
            for rep in result.reports
        ],
        "passed": result.passed(fail_on),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _decimal_arg(text):
    try:
        return Decimal(text)
    except InvalidOperation:
        raise argparse.ArgumentTypeError(f"not a number: {text!r}")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="bundlecheck",
        description="Offline translation-catalog checker for key = value locale files.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="compare target locales against a source catalog")
    check.add_argument("source", help="source catalog, e.g. en.txt")
    check.add_argument("targets", nargs="+", help="one or more target locale files")
    check.add_argument("--fail-on", choices=sorted(FAIL_LEVELS), default="error",
                       help="which level makes the exit code 1 (default: error)")
    check.add_argument("--min-coverage", type=_decimal_arg, default=Decimal("0"), metavar="PCT",
                       help="minimum translated percentage per locale")
    check.add_argument("--min-ratio", type=float, default=0.5,
                       help="lowest acceptable target/source length ratio (default: 0.5)")
    check.add_argument("--max-ratio", type=float, default=1.8,
                       help="highest acceptable target/source length ratio (default: 1.8)")
    check.add_argument("--allow-same", action="append", default=[], metavar="KEY",
                       help="key that may stay identical to the source (repeatable)")
    check.add_argument("--json", action="store_true", help="print a JSON report")

    stubs = sub.add_parser("stubs", help="write stubs for keys missing from a target")
    stubs.add_argument("source")
    stubs.add_argument("target")
    stubs.add_argument("-o", "--output", help="write to this file instead of stdout")

    lint = sub.add_parser("lint", help="lint catalog files on their own")
    lint.add_argument("files", nargs="+")
    lint.add_argument("--fail-on", choices=sorted(FAIL_LEVELS), default="error")

    return parser


def _cmd_check(args):
    if args.min_ratio > args.max_ratio:
        print("bundlecheck: --min-ratio must not exceed --max-ratio", file=sys.stderr)
        return 2
    source = load_catalog(args.source)
    targets = [load_catalog(path) for path in args.targets]
    opts = Options(
        min_coverage=args.min_coverage,
        min_ratio=args.min_ratio,
        max_ratio=args.max_ratio,
        allow_same=frozenset(args.allow_same),
    )
    result = run_check(source, targets, opts)
    if args.json:
        print(render_json(result, args.fail_on))
    else:
        print(render_text(result, args.fail_on))
    return 0 if result.passed(args.fail_on) else 1


def _cmd_stubs(args):
    source = load_catalog(args.source)
    target = load_catalog(args.target)
    text, count = stub_text(source, target)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {count} stub(s) to {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_lint(args):
    issues = []
    lines = []
    for path in args.files:
        cat = load_catalog(path)
        file_issues = list(cat.issues) + empty_source_issues(cat)
        issues.extend(file_issues)
        lines.append(f"file {path}: {len(cat.entries)} key(s)")
        if not file_issues:
            lines.append("  no issues")
        lines += [_fmt(i) for i in file_issues]
        lines.append("")
    passed = gate(issues, args.fail_on)
    lines.append(_summary(issues, passed))
    print("\n".join(lines))
    return 0 if passed else 1


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "check":
            return _cmd_check(args)
        if args.command == "stubs":
            return _cmd_stubs(args)
        return _cmd_lint(args)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"bundlecheck: cannot read input: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
