# FieldCall

FieldCall helps community event organizers record an outdoor event by address or coordinates, set personal weather limits, and compare those limits with a source-linked forecast. Address search uses Google Maps Geocoding when configured; the user confirms a match and the coordinates remain visible and editable. Each explicit recheck is stored with its source and timestamps. The result is informational, never an event safety recommendation.

**Track:** Alexa+, using the event-permitted simulated Alexa+ web experience. The browser bridge invokes a real local MCP server over Streamable HTTP; the chat host is a small deterministic router, not Alexa+ or an LLM. MET Norway Locationforecast is the intended global source. Global model resolution and available periods vary by region; no live MET Norway request has yet been verified because a compliant, participant-controlled contact identifier is not configured.

## Verification now

- `VERIFIED`: local MCP Streamable HTTP handshake negotiated MCP `2025-11-25`; the server exposed three tools; local list call returned a genuine empty database.
- `VERIFIED`: ten isolated tests pass, including signed-session checks and per-account event isolation. A local smoke run exercised the landing page, Google authorization redirect, protected dashboard, real MCP-backed event save/list, and cross-account separation using a temporary database. No test records remain.
- `IMPLEMENTED / UNVERIFIED`: Google authorization-code exchange and Google ID-token verification; live MET Norway forecast retrieval and persisted evaluation.
- `IMPLEMENTED / UNVERIFIED`: server-side Google Maps Geocoding lookup with user confirmation, no typeahead calls, visible coordinate fields, and an application limit of 100 calls per UTC day. A real Maps API key and live geocoding request have not been exercised in this environment.
- `BLOCKED`: Google credentials are not configured here, so the real Google sign-in was not completed. No live MET Norway forecast response is claimed. Docker engine access and the configured OAuth credentials are needed for the Compose/Playwright run.
- No Amazon device is needed for the rules' Alexa+ simulated web route. Official event FAQ says the Alexa+ SDK, Toolkit, CLI, and official Web Simulator are unavailable to participants; the rules permit using your own web MCP-client simulation, so no Amazon device is needed.
- The project repository and Render deployment were provided by the entrant. A public demonstration video and final Devpost submission receipt are not yet recorded here.

## Run locally

Use Python 3.12+, make a virtual environment, install `requirements.txt`, and create a local `.env` file (it is ignored by Git). Configure the variables below. Add `http://localhost:8080/auth/callback` to that OAuth client's authorized redirect URIs. Address lookup additionally needs `GOOGLE_MAPS_API_KEY`; it is optional, and manual coordinates continue to work without it. Never put credentials in GitHub.

Required local settings: `WEATHER_USER_AGENT` (app identifier plus a contact method you control), `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` (exact OAuth callback), and `SESSION_SIGNING_KEY` (random value of at least 32 characters). Optional settings: `GOOGLE_MAPS_API_KEY` (server-side Geocoding API key), `FIELDCALL_HOST` (default `127.0.0.1`), `FIELDCALL_PORT` (default `8080`), `DATABASE_PATH` (default `data/fieldcall.sqlite3`), and `WEATHER_TIMEOUT_SECONDS` (default `12`, allowed range `2..60`).

```powershell
# Create .env locally with the settings listed above; do not commit it.
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8080`. Missing/invalid config fails at startup. No weather fixtures, fake records, or provider fallback are included in the product. Tests:

```powershell
python -m unittest discover -s tests -v
```

## Render deployment

The root `render.yaml` defines a paid 0.5 CPU / 512 MB Docker web service in Frankfurt with `/health` checks. Configure the Google OAuth web client before deploying these changes. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in Render and authorize the exact callback `https://fieldcall.onrender.com/auth/callback`; Render generates `SESSION_SIGNING_KEY` in a Blueprint deployment. Set the consent audience to External and request only `openid`, `email`, and `profile`. Users sign in with Google, and events are partitioned by Google's verified stable subject ID. Judges can use their own Google account; no shared password is needed. If this is an existing Render service rather than a Blueprint, add every variable manually in its Environment settings. The local variable names and purposes are listed above.

The selected paid Render compute does not sleep from inactivity, but its filesystem is still ephemeral unless you attach a persistent disk. FieldCall's SQLite event records, geocoding daily counter, and history can disappear on restart or redeploy without persistent storage. For Google address search, add `GOOGLE_MAPS_API_KEY` to Render's Environment settings; keep the key server-side and restrict it to the Geocoding API. Set the Google Cloud Geocoding API per-day quota to 100 requests as an additional billing guard. The live site still needs real Google sign-in, geocoding, and MET Norway requests to verify those integrations.

The public repository includes setup and testing instructions here. Local research notes are maintained separately and are not required to run the product.

## Real data flow

Browser address -> authenticated server-side Google Maps Geocoding request -> user selects a returned place -> visible editable latitude/longitude -> validated event -> SQLite -> actual MCP `recheck_outdoor_event` call -> fixed MET Norway Locationforecast URL -> response expiry cache and schema validation -> full time-window comparison against user-set tolerances -> durable evaluation -> actual MCP list/read-back in UI. Address search is only sent when the user presses the lookup button, never on every keystroke. Coordinates are restricted to four decimal places in MET requests. Cached forecast data keeps its original fetch time and is labeled cached. A provider outage or incomplete period remains an error/unknown, never a successful-looking fallback.

Temperature is Celsius, wind is converted from m/s to km/h, and forecast precipitation amount is mm. MET Norway does not provide alert lookup in this integration; it is not represented as an alert-enabled product. The forecast may have coarser global resolution outside Nordic/Arctic regions and has no delivery SLA. FieldCall shows provider attribution and a CC BY 4.0 link, marks its comparison as derived, and does not imply MET endorsement.

This web interface is the event-authorized simulated Alexa+ experience; it has not been deployed to Alexa+. No AI is used: the task is bounded deterministic retrieval/comparison, and the host router only supports listing and rechecking saved event IDs.

