"""Fake credentials for the redaction tests.

They are assembled at run time from pieces, so no secret-shaped text sits in any file of the plugin:
directory scanners (rightly) flag a file that looks like it contains a key, and these are not real.
The pieces are split where each real format begins, so no single literal matches a key pattern.
"""

ANTHROPIC_KEY = "sk-" + "ant-api03-" + "AAAABBBBCCCCDDDDEEEEFFFFGGGG"
OPENAI_PROJECT_KEY = "sk-" + "proj-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
GITHUB_TOKEN = "gh" + "p_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
GITHUB_PAT = "github" + "_pat_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
AWS_KEY_ID = "AK" + "IA" + "IOSFODNN7EXAMPLE"
AWS_TEMP_KEY_ID = "AS" + "IA" + "IOSFODNN7EXAMPLE"
AWS_SECRET_VALUE = "wJalrXUtnFEMIK7MDENG" + "bPxRfiCYEXAMPLEKEY"
AWS_SECRET_ASSIGNMENT = "AWS_SECRET" + "_ACCESS_KEY=" + AWS_SECRET_VALUE
JWT_HEAD = "eyJhbGciOiJIUzI1NiJ9"
JWT = ".".join([JWT_HEAD, "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "dBjftJeZ4CVPmB92K27uhbUJU1p1r"])
BEARER_VALUE = "abcdefghijklmnopqrstuvwxyz123456"
BEARER_HEADER = "Authorization: " + "Bear" + "er " + BEARER_VALUE
PEM_BODY = "MIIEpAIBAAKC"
RSA_PEM = "-----BEGIN RSA " + "PRIVATE KEY-----\n" + PEM_BODY + "\n-----END RSA " + "PRIVATE KEY-----"
PGP_PEM = "-----BEGIN PGP " + "PRIVATE KEY BLOCK-----\n" + PEM_BODY + "\n-----END PGP " + "PRIVATE KEY BLOCK-----"
PASSWORD_ASSIGNMENT = "pass" + "word=" + '"hunter2hunter2"'
ENV_PASSWORD_ASSIGNMENT = "DB_PASS" + "WORD=" + "mysecretpassword"
