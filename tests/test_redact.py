import unittest

from orchestra.redact import scrub, scrub_obj
from tests import fake_secrets as fake


class TestScrub(unittest.TestCase):
    def test_anthropic_key(self):
        out = scrub("use " + fake.ANTHROPIC_KEY + " now")
        self.assertNotIn("AAAABBBB", out)
        self.assertIn("redacted:anthropic_key", out)

    def test_github_token(self):
        out = scrub("token " + fake.GITHUB_TOKEN)
        self.assertIn("redacted:github_token", out)
        self.assertNotIn("ABCDEFGHIJ", out)

    def test_aws_access_key_id(self):
        out = scrub(fake.AWS_KEY_ID)
        self.assertIn("redacted:aws_key_id", out)
        self.assertNotIn(fake.AWS_KEY_ID, out)

    def test_bearer_header(self):
        out = scrub(fake.BEARER_HEADER)
        self.assertIn("redacted:bearer", out)
        self.assertNotIn(fake.BEARER_VALUE, out)

    def test_jwt(self):
        out = scrub(fake.JWT)
        self.assertIn("redacted:jwt", out)
        self.assertNotIn(fake.JWT_HEAD, out)

    def test_private_key_block(self):
        out = scrub(fake.RSA_PEM)
        self.assertIn("redacted:private_key", out)
        self.assertNotIn(fake.PEM_BODY, out)

    def test_assignment_keeps_the_key_name(self):
        out = scrub(fake.PASSWORD_ASSIGNMENT)
        self.assertIn("password", out)
        self.assertNotIn("hunter2hunter2", out)

    def test_ordinary_prose_is_untouched(self):
        text = "Read src/main.py and return a summary of the sk-learn usage."
        self.assertEqual(scrub(text), text)

    def test_empty_and_none_safe(self):
        self.assertEqual(scrub(""), "")
        self.assertIsNone(scrub(None))

    def test_prefixed_env_assignment(self):
        out = scrub(fake.ENV_PASSWORD_ASSIGNMENT)
        self.assertIn("redacted:secret", out)
        self.assertNotIn("mysecretpassword", out)
        self.assertIn("DB_PASSWORD", out)

    def test_aws_secret_key_assignment(self):
        out = scrub(fake.AWS_SECRET_ASSIGNMENT)
        self.assertIn("redacted:secret", out)
        self.assertNotIn(fake.AWS_SECRET_VALUE, out)
        self.assertIn("AWS_SECRET_ACCESS_KEY", out)

    def test_sk_proj_key(self):
        out = scrub("use " + fake.OPENAI_PROJECT_KEY + " now")
        self.assertIn("redacted:openai_key", out)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", out)

    def test_github_pat_token(self):
        out = scrub("token " + fake.GITHUB_PAT)
        self.assertIn("redacted:github_token", out)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", out)

    def test_pgp_private_key_block(self):
        out = scrub(fake.PGP_PEM)
        self.assertIn("redacted:private_key", out)
        self.assertNotIn(fake.PEM_BODY, out)

    def test_asia_key_id(self):
        out = scrub("use " + fake.AWS_TEMP_KEY_ID + " for temp creds")
        self.assertIn("redacted:aws_key_id", out)
        self.assertNotIn(fake.AWS_TEMP_KEY_ID, out)


class TestScrubObj(unittest.TestCase):
    def test_recurses_nested_structures(self):
        obj = {"a": [fake.GITHUB_TOKEN, {"b": "clean"}], "n": 5}
        out = scrub_obj(obj)
        self.assertIn("redacted:github_token", out["a"][0])
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", out["a"][0])
        self.assertEqual(out["a"][1]["b"], "clean")
        self.assertEqual(out["n"], 5)


if __name__ == "__main__":
    unittest.main()
