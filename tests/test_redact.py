import unittest

from orchestra.redact import scrub, scrub_obj


class TestScrub(unittest.TestCase):
    def test_anthropic_key(self):
        out = scrub("use sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG now")
        self.assertNotIn("AAAABBBB", out)
        self.assertIn("redacted:anthropic_key", out)

    def test_github_token(self):
        out = scrub("token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        self.assertIn("redacted:github_token", out)
        self.assertNotIn("ABCDEFGHIJ", out)

    def test_aws_access_key_id(self):
        self.assertIn("redacted:aws_key_id", scrub("AKIAIOSFODNN7EXAMPLE"))

    def test_bearer_header(self):
        out = scrub("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123456")
        self.assertIn("redacted:bearer", out)

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r"
        self.assertIn("redacted:jwt", scrub(jwt))

    def test_private_key_block(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKC\n-----END RSA PRIVATE KEY-----"
        out = scrub(pem)
        self.assertIn("redacted:private_key", out)
        self.assertNotIn("MIIEpAIBAAKC", out)

    def test_assignment_keeps_the_key_name(self):
        out = scrub('password="hunter2hunter2"')
        self.assertIn("password", out)
        self.assertNotIn("hunter2hunter2", out)

    def test_ordinary_prose_is_untouched(self):
        text = "Read src/main.py and return a summary of the sk-learn usage."
        self.assertEqual(scrub(text), text)

    def test_empty_and_none_safe(self):
        self.assertEqual(scrub(""), "")
        self.assertIsNone(scrub(None))


class TestScrubObj(unittest.TestCase):
    def test_recurses_nested_structures(self):
        obj = {"a": ["ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", {"b": "clean"}], "n": 5}
        out = scrub_obj(obj)
        self.assertIn("redacted:github_token", out["a"][0])
        self.assertEqual(out["a"][1]["b"], "clean")
        self.assertEqual(out["n"], 5)


if __name__ == "__main__":
    unittest.main()
