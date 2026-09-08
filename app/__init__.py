"""Load environment variables from a local .env file, if present.

Runs on `import app`, before app.llm reads OPENAI_API_KEY at import time.
"""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass
