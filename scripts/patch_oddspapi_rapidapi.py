from __future__ import annotations

from pathlib import Path

PATH = Path('app/providers/oddspapi.py')

IMPORT_OLD = "import json\nimport re\n"
IMPORT_NEW = "import json\nimport os\nimport re\n"

INIT_OLD = """        self.settings = settings
        self.api_key = getattr(settings, \"oddspapi_api_key\", None)
        self.base_url = str(getattr(settings, \"oddspapi_base_url\", \"https://api.oddspapi.io/v4\")).rstrip(\"/\")
        self.timeout = float(getattr(settings, \"oddspapi_timeout_seconds\", 12.0) or 12.0)
"""

INIT_NEW = """        self.settings = settings
        self.api_key = getattr(settings, \"oddspapi_api_key\", None)
        self.base_url = str(getattr(settings, \"oddspapi_base_url\", \"https://api.oddspapi.io/v4\")).rstrip(\"/\")
        self.rapidapi_enabled = str(os.getenv(\"ODDSPAPI_RAPIDAPI_ENABLED\", \"false\")).strip().lower() in {\"1\", \"true\", \"yes\", \"on\"}
        self.rapidapi_host = str(os.getenv(\"ODDSPAPI_RAPIDAPI_HOST\") or \"odds-api1.p.rapidapi.com\").strip()
        self.rapidapi_use_query_api_key = str(os.getenv(\"ODDSPAPI_RAPIDAPI_USE_QUERY_API_KEY\", \"false\")).strip().lower() in {\"1\", \"true\", \"yes\", \"on\"}
        if self.rapidapi_enabled:
            rapidapi_key = str(
                os.getenv(\"ODDSPAPI_RAPIDAPI_KEY\")
                or os.getenv(\"ODDSPAPI_API_KEY\")
                or os.getenv(\"RAPIDAPI_KEY\")
                or self.api_key
                or \"\"
            ).strip()
            self.api_key = rapidapi_key
            self.base_url = str(os.getenv(\"ODDSPAPI_BASE_URL\") or f\"https://{self.rapidapi_host}\").rstrip(\"/\")
        self.timeout = float(getattr(settings, \"oddspapi_timeout_seconds\", 12.0) or 12.0)
"""

REQUEST_OLD = """                response = await client.get(f\"{self.base_url}{path}\", params=params)
"""

REQUEST_NEW = """                request_params = dict(params or {})
                headers = None
                if self.rapidapi_enabled:
                    headers = {
                        \"x-rapidapi-host\": self.rapidapi_host,
                        \"x-rapidapi-key\": str(self.api_key or \"\"),
                    }
                    if not self.rapidapi_use_query_api_key:
                        request_params.pop(\"apiKey\", None)
                response = await client.get(f\"{self.base_url}{path}\", params=request_params, headers=headers)
                if self.rapidapi_enabled and response.status_code == 404 and self.base_url.rstrip(\"/\").endswith(\"/v4\"):
                    root_url = self.base_url.rstrip(\"/\")[:-3].rstrip(\"/\")
                    response = await client.get(f\"{root_url}{path}\", params=request_params, headers=headers)
"""


def main() -> int:
    if not PATH.exists():
        print(f'skip: {PATH} not found')
        return 0
    src = PATH.read_text(encoding='utf-8')
    original = src
    if 'import os' not in src:
        src = src.replace(IMPORT_OLD, IMPORT_NEW, 1)
    if 'self.rapidapi_enabled' not in src:
        if INIT_OLD not in src:
            print('warn: oddspapi init block not found')
        else:
            src = src.replace(INIT_OLD, INIT_NEW, 1)
    if 'x-rapidapi-host' not in src:
        if REQUEST_OLD not in src:
            print('warn: oddspapi request block not found')
        else:
            src = src.replace(REQUEST_OLD, REQUEST_NEW, 1)
    if 'https://{self.rapidapi_host}/v4' in src:
        src = src.replace('https://{self.rapidapi_host}/v4', 'https://{self.rapidapi_host}')
    if 'root_url = self.base_url.rstrip("/")[:-3].rstrip("/")' not in src and 'response = await client.get(f"{self.base_url}{path}", params=request_params, headers=headers)' in src:
        src = src.replace(
            '                response = await client.get(f"{self.base_url}{path}", params=request_params, headers=headers)\n',
            REQUEST_NEW,
            1,
        )
    if src != original:
        PATH.write_text(src, encoding='utf-8')
        print(f'patched: {PATH}')
    else:
        print(f'already patched or no changes: {PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
