# FieldCall

FieldCall helps community event organizers record an outdoor event, their personal weather limits, and compare those limits with a source-linked forecast for coordinates worldwide. Each explicit recheck is stored with its source and timestamps. The result is an informational comparison, never an event safety recommendation.

**Track:** Alexa+, using the event-permitted simulated Alexa+ web experience. The browser bridge invokes a real local MCP server over Streamable HTTP; the chat host is a small deterministic router, not Alexa+ or an LLM. MET Norway Locationforecast is the intended global source. Global model resolution and available periods vary by region; no live MET Norway request has yet been verified because a compliant, participant-controlled contact identifier is not configured.

## Verification now

- `VERIFIED`: local MCP Streamable HTTP handshake negotiated MCP `2025-11-25`; the server exposed three tools; local list call returned a genuine empty database.
- `VERIFIED`: eight isolated validation, normalization, coverage, unit-conversion and conservative-comparison tests pass. Their inputs are unit fixtures only, never product records.
- `IMPLEMENTED / UNVERIFIED`: event save, global forecast retrieval, MET expiry-aware cache, comparison persistence/read-back and recheck history.
- `BLOCKED`: live MET provider call needs a real `WEATHER_USER_AGENT` contact you control. No live response is claimed. Docker engine permissions and offline npm registry also prevent container and Playwright verification.
- No Amazon device is needed for the rules' Alexa+ simulated web route. Official event FAQ says the Alexa+ SDK, Toolkit, CLI, and official Web Simulator are unavailable to participants; the rules permit using your own web MCP-client simulation, so no Amazon device is needed.
- No public GitHub repo, recording, Devpost entry, or receipt has been created.

## Run locally

Use Python 3.12+, make a virtual environment, install `requirements.txt`, copy `.env.example` to `.env`, and set `WEATHER_USER_AGENT` to an identifying app string plus a contact method you control. MET Norway requires a unique User-Agent. Never use the example value as if it were your contact.

```powershell
Copy-Item .env.example .env -Force
# Edit WEATHER_USER_AGENT with your actual contact method.
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

The root `render.yaml` defines a paid 0.5 CPU / 512 MB Docker web service in Frankfurt with `/health` checks. Connect this GitHub repository in Render as a Blueprint and deploy it. Render supplies `PORT` and the app binds to it; set `WEATHER_USER_AGENT` in the Blueprint and Render generates `FIELDCALL_ACCESS_PASSWORD`. Open the service URL and use username `fieldcall` with the generated password from the Render environment settings. Keep that password private; share it with judges only in the testing instructions if needed.

Render Free sleeps after 15 minutes idle and uses an ephemeral filesystem. FieldCall's SQLite event records and history can disappear when the service sleeps, restarts, or redeploys. This deployment is suitable for a short, fresh demo, not durable event storage. The live site still needs a real MET Norway request to verify the provider integration.

See [mechanism](docs/mechanism.md), [deployment](docs/deployment.md), [selection](docs/hackathon-analysis.md), [rubric/claims](docs/rubric-matrix.md), [competitors](docs/competitive-analysis.md), [dependency/security review](docs/dependency-audit.md), [demo plan](docs/demo-and-pitch.md), [evidence index](evidence/README.md), and [product feedback](docs/product-feedback.md).

## Real data flow

Browser form -> validated event -> SQLite -> actual MCP `recheck_outdoor_event` call -> fixed MET Norway Locationforecast URL -> response expiry cache and schema validation -> full time-window comparison against user-set tolerances -> durable evaluation -> actual MCP list/read-back in UI. Coordinates are restricted to four decimal places in requests. Cached data keeps its original fetch time and is labeled cached. A provider outage or incomplete period remains an error/unknown, never a successful-looking fallback.

Temperature is Celsius, wind is converted from m/s to km/h, and forecast precipitation amount is mm. MET Norway does not provide alert lookup in this integration; it is not represented as an alert-enabled product. The forecast may have coarser global resolution outside Nordic/Arctic regions and has no delivery SLA. FieldCall shows provider attribution and a CC BY 4.0 link, marks its comparison as derived, and does not imply MET endorsement.

This web interface is the event-authorized simulated Alexa+ experience; it has not been deployed to Alexa+. No AI is used: the task is bounded deterministic retrieval/comparison, and the host router only supports listing and rechecking saved event IDs.

