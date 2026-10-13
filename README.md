# FieldCall

FieldCall helps community event organizers record an outdoor event, their personal weather limits, and compare those limits with a source-linked forecast for coordinates worldwide. Each explicit recheck is stored with its source and timestamps. The result is an informational comparison, never an event safety recommendation.

**Track:** Alexa+, using the event-permitted simulated Alexa+ web experience. The browser bridge invokes a real local MCP server over Streamable HTTP; the chat host is a small deterministic router, not Alexa+ or an LLM. MET Norway Locationforecast is the intended global source. Global model resolution and available periods vary by region; no live MET Norway request has yet been verified because a compliant, participant-controlled contact identifier is not configured.

## Verification now

- `VERIFIED`: local MCP Streamable HTTP handshake negotiated MCP `2025-11-25`; the server exposed three tools; local list call returned a genuine empty database.
- `VERIFIED`: ten isolated tests pass, including signed-session checks and per-account event isolation. A local smoke run exercised the landing page, Google authorization redirect, protected dashboard, real MCP-backed event save/list, and cross-account separation using a temporary database. No test records remain.
- `IMPLEMENTED / UNVERIFIED`: Google authorization-code exchange and Google ID-token verification; live MET Norway forecast retrieval and persisted evaluation.
- `BLOCKED`: Google credentials are not configured here, so the real Google sign-in was not completed. No live MET Norway forecast response is claimed. Docker engine access and the configured OAuth credentials are needed for the Compose/Playwright run.
- No Amazon device is needed for the rules' Alexa+ simulated web route. Official event FAQ says the Alexa+ SDK, Toolkit, CLI, and official Web Simulator are unavailable to participants; the rules permit using your own web MCP-client simulation, so no Amazon device is needed.
- The project repository and Render deployment were provided by the entrant. A public demonstration video and final Devpost submission receipt are not yet recorded here.

## Run locally

Use Python 3.12+, make a virtual environment, install `requirements.txt`, and copy `.env.example` to `.env`. Configure `WEATHER_USER_AGENT`, Google OAuth web-client credentials, the exact local redirect URI, and a random `SESSION_SIGNING_KEY`. Add `http://localhost:8080/auth/callback` to that OAuth client's authorized redirect URIs. MET Norway requires an identifying User-Agent. Never put credentials in GitHub.

```powershell
Copy-Item .env.example .env -Force
# Add your Google OAuth client settings and session signing key as well.
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

The root `render.yaml` defines a paid 0.5 CPU / 512 MB Docker web service in Frankfurt with `/health` checks. Configure the Google OAuth web client before deploying these changes. Set `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET` in Render and authorize the exact callback `https://fieldcall.onrender.com/auth/callback`; Render generates `SESSION_SIGNING_KEY` in a Blueprint deployment. Set the consent audience to External and request only `openid`, `email`, and `profile`. Users sign in with Google, and events are partitioned by Google's verified stable subject ID. Judges can use their own Google account; no shared password is needed. If this is an existing Render service rather than a Blueprint, add every variable manually in its Environment settings. See `.env.example` for local configuration.

The selected paid Render compute does not sleep from inactivity, but its filesystem is still ephemeral unless you attach a persistent disk. FieldCall's SQLite event records and history can disappear on restart or redeploy without persistent storage. The live site still needs a real Google sign-in and MET Norway request to verify those integrations.

The public repository includes setup and testing instructions here. Local research notes are maintained separately and are not required to run the product.

## Real data flow

Browser form -> validated event -> SQLite -> actual MCP `recheck_outdoor_event` call -> fixed MET Norway Locationforecast URL -> response expiry cache and schema validation -> full time-window comparison against user-set tolerances -> durable evaluation -> actual MCP list/read-back in UI. Coordinates are restricted to four decimal places in requests. Cached data keeps its original fetch time and is labeled cached. A provider outage or incomplete period remains an error/unknown, never a successful-looking fallback.

Temperature is Celsius, wind is converted from m/s to km/h, and forecast precipitation amount is mm. MET Norway does not provide alert lookup in this integration; it is not represented as an alert-enabled product. The forecast may have coarser global resolution outside Nordic/Arctic regions and has no delivery SLA. FieldCall shows provider attribution and a CC BY 4.0 link, marks its comparison as derived, and does not imply MET endorsement.

This web interface is the event-authorized simulated Alexa+ experience; it has not been deployed to Alexa+. No AI is used: the task is bounded deterministic retrieval/comparison, and the host router only supports listing and rechecking saved event IDs.

