import unittest

from orchestra.extract import extract_expected_output, extract_objective


BRIEF_WITH_HEADING = """You are implementing Task 4.

## Context

Some background about the repo.

## Deliverable

A passing test suite and a commit.
Report the commit SHA.

## Notes

Ignore the linter.
"""

BRIEF_WITH_IMPERATIVE = """You are reviewing a diff.

Look at the changes carefully.
Return a list of findings, most severe first.
Do not fix anything.
"""

BRIEF_PLAIN = """## Objective

Investigate why the build is slow on Windows.

Then figure out what the biggest contributor is and keep going.
"""

BRIEF_NO_SIGNALS = """Investigate why the build is slow on Windows and figure out
what the biggest contributor is.

Then keep going.
"""


class TestExpectedOutput(unittest.TestCase):
    def test_tier1_heading_wins(self):
        e = extract_expected_output(BRIEF_WITH_HEADING, "Implement task 4")
        self.assertIn("passing test suite", e.text)
        self.assertIn("Report the commit SHA", e.text)
        self.assertNotIn("Ignore the linter", e.text)
        self.assertIn("Deliverable", e.source)

    def test_tier1_is_case_insensitive_and_level_agnostic(self):
        brief = "# Job\n\n### expected output\n\nA JSON blob.\n"
        e = extract_expected_output(brief, "")
        self.assertEqual(e.text.strip(), "A JSON blob.")

    def test_tier1_section_ends_at_same_or_higher_heading(self):
        brief = "## Output\n\nThe answer.\n\n### Sub\n\nStill the answer.\n\n## Other\n\nNope.\n"
        e = extract_expected_output(brief, "")
        self.assertIn("Still the answer", e.text)
        self.assertNotIn("Nope", e.text)

    def test_tier2_imperative_line(self):
        e = extract_expected_output(BRIEF_WITH_IMPERATIVE, "Review the diff")
        self.assertIn("Return a list of findings", e.text)
        self.assertEqual(e.source, "imperative line")

    def test_tier3_fallback_uses_description_and_first_paragraph(self):
        e = extract_expected_output(BRIEF_PLAIN, "Investigate build speed")
        self.assertIn("Investigate build speed", e.text)
        self.assertEqual(e.source, "fallback")

    def test_empty_brief_is_safe(self):
        e = extract_expected_output("", "")
        self.assertEqual(e.text, "")
        self.assertEqual(e.source, "none")

    def test_new_heading_conventions_are_recognized(self):
        brief = "# Job\n\n### report contract\n\nReturn JSON with a `status` key.\n"
        e = extract_expected_output(brief, "")
        self.assertEqual(e.text.strip(), "Return JSON with a `status` key.")
        self.assertIn("report contract", e.source)

    def test_fallback_that_genuinely_differs_from_objective_is_kept(self):
        e = extract_expected_output(BRIEF_PLAIN, "Investigate build speed")
        objective = extract_objective(BRIEF_PLAIN, "Investigate build speed")
        self.assertNotEqual(e.text.strip(), objective.text.strip())
        self.assertEqual(e.source, "fallback")
        self.assertIn("Investigate build speed", e.text)

    def test_fallback_that_duplicates_objective_is_not_stated(self):
        e = extract_expected_output(BRIEF_NO_SIGNALS, "Investigate build speed")
        objective = extract_objective(BRIEF_NO_SIGNALS, "Investigate build speed")
        # Both would independently fall to the same description+first-paragraph
        # fallback text -- confirm that premise before checking the fix.
        self.assertEqual(objective.source, "fallback")
        self.assertEqual(e.text, "")
        self.assertEqual(e.source, "not stated")


class TestObjective(unittest.TestCase):
    def test_objective_heading(self):
        brief = "## Objective\n\nMake the tests pass.\n\n## Deliverable\n\nA commit.\n"
        e = extract_objective(brief, "")
        self.assertEqual(e.text.strip(), "Make the tests pass.")

    def test_objective_you_are_line(self):
        e = extract_objective(BRIEF_WITH_IMPERATIVE, "")
        self.assertIn("reviewing a diff", e.text)

    def test_objective_falls_back_to_description(self):
        e = extract_objective("Some text with no signals at all.", "Fix the parser")
        self.assertIn("Fix the parser", e.text)


if __name__ == "__main__":
    unittest.main()
