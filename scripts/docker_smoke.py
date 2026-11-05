"""Container ohne Modelldownload auf Start, UI und Schlüsselpflicht prüfen."""

import sys
import time
import urllib.error
import urllib.request

url = sys.argv[1].rstrip("/")
for attempt in range(60):
    try:
        with urllib.request.urlopen(url + "/", timeout=2) as response:
            assert "Koprotokoll" in response.read().decode()
        break
    except (OSError, AssertionError):
        if attempt == 59:
            raise
        time.sleep(1)
try:
    urllib.request.urlopen(url + "/api/meetings", timeout=3)
except urllib.error.HTTPError as error:
    assert error.code == 401, error.code
else:
    raise AssertionError("Container erlaubt API-Zugriff ohne Schlüssel")
print("Oberfläche erreichbar; API ohne Schlüssel abgewiesen.")
