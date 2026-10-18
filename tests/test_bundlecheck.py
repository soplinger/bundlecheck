import io
import json
import os
import contextlib
import sys
import tempfile
import unittest
from decimal import Decimal

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import bundlecheck as bc  # noqa: E402

EXAMPLES = os.path.join(ROOT, "examples")


def run_cli(argv):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bc.main(argv)
    return code, out.getvalue(), err.getvalue()


def codes(issues):
    return sorted(i.code for i in issues)


class ParseTests(unittest.TestCase):
    def test_parses_keys_skipping_comments_and_blank_lines(self):
        cat = bc.parse_text("# head\n\na = Alpha\n  # note\nb = Beta\n")
        self.assertEqual(list(cat.entries), ["a", "b"])
        self.assertEqual(cat.entries["a"].value, "Alpha")
        self.assertEqual(cat.issues, [])

    def test_records_line_numbers(self):
        cat = bc.parse_text("# c\n\nfirst = 1\nsecond = 2\n")
        self.assertEqual(cat.entries["first"].line, 3)
        self.assertEqual(cat.entries["second"].line, 4)

    def test_line_without_equals_is_a_syntax_error_with_line_number(self):
        cat = bc.parse_text("ok = yes\nbroken line\n")
        self.assertEqual(len(cat.issues), 1)
        issue = cat.issues[0]
        self.assertEqual((issue.code, issue.line, issue.level), ("syntax", 2, "error"))

    def test_invalid_key_is_reported_and_skipped(self):
        cat = bc.parse_text("bad key = value\n")
        self.assertEqual(codes(cat.issues), ["bad-key"])
        self.assertNotIn("bad key", cat.entries)

    def test_duplicate_key_keeps_first_and_names_both_lines(self):
        cat = bc.parse_text("k = one\nk = two\n")
        self.assertEqual(cat.entries["k"].value, "one")
        self.assertEqual(codes(cat.issues), ["duplicate"])
        self.assertIn("line 1", cat.issues[0].message)
        self.assertEqual(cat.issues[0].line, 2)

    def test_quoted_values_keep_spaces_and_unescape(self):
        cat = bc.parse_text('k = "  padded\\tand \\"quoted\\"\\nline  "\n')
        self.assertEqual(cat.entries["k"].value, '  padded\tand "quoted"\nline  ')

    def test_unterminated_quote_is_an_error(self):
        cat = bc.parse_text('k = "open\n')
        self.assertEqual(codes(cat.issues), ["syntax"])
        self.assertNotIn("k", cat.entries)

    def test_stray_inner_quote_is_rejected_not_mangled(self):
        for text in ['k = "OK" or "Cancel"\n', 'k = "ends\\"\n']:
            cat = bc.parse_text(text)
            self.assertEqual(codes(cat.issues), ["syntax"], msg=text)
            self.assertNotIn("k", cat.entries, msg=text)

    def test_escaped_trailing_backslash_still_parses(self):
        cat = bc.parse_text('k = "abc\\\\"\n')
        self.assertEqual(cat.issues, [])
        self.assertEqual(cat.entries["k"].value, "abc\\")

    def test_file_with_byte_order_mark_loads_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bom.txt")
            with open(path, "w", encoding="utf-8-sig") as f:
                f.write("# c\na = x\n")
            cat = bc.load_catalog(path)
            self.assertEqual(cat.issues, [])
            self.assertIn("a", cat.entries)

    def test_serialize_round_trips_tricky_values(self):
        for value in ["plain", "", "  spaced  ", 'say "hi"', "back\\slash", "two\nlines"]:
            text = f"k = {bc.serialize_value(value)}\n"
            self.assertEqual(bc.parse_text(text).entries["k"].value, value, msg=repr(value))


class PlaceholderTests(unittest.TestCase):
    def test_reordered_placeholders_are_accepted(self):
        self.assertIsNone(bc.placeholder_problem("{a} of {b}", "{b} sur {a}"))

    def test_missing_and_unexpected_placeholders_are_named(self):
        problem = bc.placeholder_problem("Hi {name}, %d new", "Salut {nom}")
        self.assertIn("missing %d, {name}", problem)
        self.assertIn("unexpected {nom}", problem)

    def test_double_percent_is_literal_and_positional_is_counted(self):
        self.assertIsNone(bc.placeholder_problem("100%% of %1$s", "100%% de %1$s"))
        self.assertIsNotNone(bc.placeholder_problem("%1$s", "%s"))


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.source = bc.parse_text("a = Save\nb = Cancel\nc = Hello {name}\n", "en")
        self.opts = bc.Options()

    def test_missing_extra_and_empty_keys(self):
        target = bc.parse_text("a = Sauver\nc =\nz = extra\n", "fr")
        report = bc.check_locale(self.source, target, self.opts)
        self.assertEqual(codes(report.issues), ["empty", "extra", "missing"])
        missing = [i for i in report.issues if i.code == "missing"][0]
        self.assertEqual(missing.key, "b")

    def test_identical_value_is_untranslated_unless_allowed(self):
        target = bc.parse_text("a = Sauver\nb = Cancel\nc = Bonjour {name}\n", "fr")
        report = bc.check_locale(self.source, target, self.opts)
        self.assertEqual(codes(report.issues), ["untranslated"])
        allowed = bc.Options(allow_same=frozenset({"b"}))
        report = bc.check_locale(self.source, target, allowed)
        self.assertEqual(report.issues, [])
        self.assertEqual(report.covered, 3)

    def test_coverage_rounds_half_up_to_one_decimal(self):
        target = bc.parse_text("a = Sauver\nb = Annuler\n", "fr")
        report = bc.check_locale(self.source, target, self.opts)
        self.assertEqual(report.covered, 2)
        self.assertEqual(report.total, 3)
        self.assertEqual(report.coverage, Decimal("66.7"))

    def test_coverage_below_minimum_is_an_error(self):
        target = bc.parse_text("a = Sauver\n", "fr")
        opts = bc.Options(min_coverage=Decimal("50"))
        report = bc.check_locale(self.source, target, opts)
        self.assertIn("coverage", codes(report.issues))
        self.assertTrue(any(i.code == "coverage" and i.level == "error" for i in report.issues))

    def test_length_ratio_outlier_is_a_warning(self):
        target = bc.parse_text("a = Sauver\nb = Annuler\nc = Bonjour {name}, ceci est une très longue phrase\n", "fr")
        report = bc.check_locale(self.source, target, self.opts)
        self.assertEqual(codes(report.issues), ["length"])
        self.assertEqual(report.issues[0].level, "warning")

    def test_placeholder_mismatch_is_an_error_on_the_right_line(self):
        target = bc.parse_text("a = Sauver\nb = Annuler\nc = Bonjour\n", "fr")
        report = bc.check_locale(self.source, target, self.opts)
        self.assertEqual(codes(report.issues), ["placeholder"])
        self.assertEqual(report.issues[0].line, 3)


class StubTests(unittest.TestCase):
    def test_stubs_contain_only_missing_keys_and_round_trip(self):
        source = bc.parse_text('a = Save\nb = "Say \\"hi\\""\nc = Cancel\n', "en")
        target = bc.parse_text("a = Sauver\n", "fr")
        text, count = bc.stub_text(source, target)
        self.assertEqual(count, 2)
        self.assertEqual(text.count("# TODO: translate"), 2)
        stubbed = bc.parse_text(text, "stub")
        self.assertEqual(stubbed.entries["b"].value, 'Say "hi"')
        self.assertNotIn("a", stubbed.entries)

    def test_nothing_missing_gives_empty_stub(self):
        source = bc.parse_text("a = x\n")
        text, count = bc.stub_text(source, bc.parse_text("a = y\n"))
        self.assertEqual((text, count), ("", 0))


class CliTests(unittest.TestCase):
    def test_example_set_fails_the_default_gate(self):
        code, out, _ = run_cli([
            "check", os.path.join(EXAMPLES, "en.txt"),
            os.path.join(EXAMPLES, "fr.txt"), os.path.join(EXAMPLES, "de.txt"),
        ])
        self.assertEqual(code, 1)
        self.assertIn("FAIL", out)
        self.assertIn("missing key 'footer.version'", out)
        self.assertIn("duplicate", out)

    def test_fail_on_never_always_exits_zero(self):
        code, _, _ = run_cli([
            "check", os.path.join(EXAMPLES, "en.txt"),
            os.path.join(EXAMPLES, "de.txt"), "--fail-on", "never",
        ])
        self.assertEqual(code, 0)

    def test_clean_target_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "en.txt")
            tgt = os.path.join(tmp, "fr.txt")
            with open(src, "w", encoding="utf-8") as f:
                f.write("a = Save\nb = Hello {name}\n")
            with open(tgt, "w", encoding="utf-8") as f:
                f.write("a = Sauver\nb = Bonjour {name}\n")
            code, out, _ = run_cli(["check", src, tgt])
            self.assertEqual(code, 0)
            self.assertIn("100.0%", out)
            self.assertIn("0 error(s), 0 warning(s) -> PASS", out)

    def test_json_report_is_valid_and_matches_gate(self):
        code, out, _ = run_cli([
            "check", os.path.join(EXAMPLES, "en.txt"),
            os.path.join(EXAMPLES, "fr.txt"), "--json", "--fail-on", "never",
        ])
        payload = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(payload["passed"])
        self.assertEqual(payload["keys"], 12)
        self.assertEqual(payload["locales"][0]["locale"], os.path.join(EXAMPLES, "fr.txt"))

    def test_unreadable_file_exits_two(self):
        code, _, err = run_cli(["check", os.path.join(EXAMPLES, "missing.txt"), os.path.join(EXAMPLES, "fr.txt")])
        self.assertEqual(code, 2)
        self.assertIn("cannot read input", err)

    def test_inverted_ratio_bounds_exit_two(self):
        code, _, err = run_cli([
            "check", os.path.join(EXAMPLES, "en.txt"), os.path.join(EXAMPLES, "fr.txt"),
            "--min-ratio", "2", "--max-ratio", "1",
        ])
        self.assertEqual(code, 2)
        self.assertIn("--min-ratio", err)

    def test_stubs_command_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "stubs.txt")
            code, out, _ = run_cli([
                "stubs", os.path.join(EXAMPLES, "en.txt"), os.path.join(EXAMPLES, "fr.txt"),
                "-o", out_path,
            ])
            self.assertEqual(code, 0)
            with open(out_path, encoding="utf-8") as f:
                stubbed = bc.parse_text(f.read(), out_path)
            self.assertEqual(stubbed.issues, [])
            self.assertIn("footer.version", stubbed.entries)
            self.assertEqual(stubbed.entries["footer.version"].value, "v%s")

    def test_lint_reports_syntax_and_duplicate_with_exit_one(self):
        code, out, _ = run_cli(["lint", os.path.join(EXAMPLES, "de.txt")])
        self.assertEqual(code, 1)
        self.assertIn("expected key = value", out)
        self.assertIn("already defined on line", out)


if __name__ == "__main__":
    unittest.main()
