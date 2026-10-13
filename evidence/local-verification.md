# Local verification record

Run date: 2026-10-13, approximately 00:44 UTC (01:44 Africa/Lagos). Project is uncommitted in a local Git repository; no commit SHA exists. `WEATHER_USER_AGENT` used for local-only server checks was a clearly synthetic `.invalid` value and must not be used for external requests.

## Commands and results

- `python -m py_compile app.py tests/test_core.py` — exit 0.
- `node --check static/app.js` — exit 0.
- `python -m unittest discover -s tests -v` — 8 tests, all passed. Isolated unit fixtures cover input validation, documented MET field normalization, Celsius/m/s/mm conversion, missing fields, incomplete coverage and within/outside/unknown comparison. No HTTP provider call occurs in these tests.
- `docker --context desktop-linux compose config --quiet` — exit 0.
- `python app.py` with no `WEATHER_USER_AGENT` and blank `.env.example` settings — failed before startup with `ValueError: WEATHER_USER_AGENT is required (8-160 characters; identify your app and contact)`. This is the intended fail-closed preflight.
- Local app boot with temporary `.invalid` User-Agent and isolated DB — `/health` returned `ok`, `/` returned HTTP 200, `/api/events` returned empty JSON list, and `/api/assistant` “List my events” returned the real no-saved-events state through MCP. No event or forecast row was created.
- Actual MCP SDK client to `http://127.0.0.1:8080/mcp` — negotiated `2025-11-25`; tool listing returned `save_outdoor_event`, `recheck_outdoor_event`, `list_outdoor_events`; call to list tool returned `isError=false`. This proves local MCP transport/tool behavior only.

## Blocked checks and scope

No MET Norway request was attempted during this verification because no participant-controlled contact string was supplied for the required User-Agent. Consequently schema normalization tests use controlled unit fixtures only; no live data, event evaluation, persistence after restart, expiry/304 response, or real global coordinate behavior is verified.

Docker engine info previously failed with named-pipe permission denied. Playwright/npm dependencies are unavailable offline and there is no `package-lock.json`; browser suite was not run. No container image, public deployment, Alexa+ service, device, demo video, repository remote, Devpost form or submission was tested/completed.

The application was stopped after local checks. No secret or personal contact value appears in this report.

