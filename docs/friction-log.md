# FieldCall — Hackathon Friction Log

These entries describe setup friction observed while preparing FieldCall for the Amazon Developer Hackathon. The proposed workarounds are documented in the project; no live provider success is implied.

## 1. Alexa+ developer-tool access

- **Task:** Find a participant-accessible way to build and demonstrate an Alexa+ add-on.
- **Steps:** Reviewed the Alexa+ add-on setup material and the hackathon rules and FAQ to determine how participants could access the developer kit and official simulator.
- **Expected:** Participant instructions would lead to a usable toolkit or an official test simulator.
- **Actual:** The event FAQ says the Alexa+ SDK, MCP Toolkit, CLI, and Web Simulator are limited to select partners and unavailable to hackathon participants.
- **Severity:** Blocker for testing against official Alexa+ tooling.
- **Workaround:** Use the rules-permitted self-hosted MCP server and a custom web client/simulation. FieldCall implements Streamable HTTP and labels the web experience as simulated.
- **Suggestion:** Put the participant access restriction and the supported self-hosted MCP/web-simulation path at the start of Alexa+ setup instructions.

## 2. Google Maps Geocoding setup

- **Task:** Resolve an event address to coordinates in FieldCall.
- **Steps:** Tried to configure Google Maps Geocoding for address lookup in the event form.
- **Expected:** Enter an address, retrieve a matching location, and confirm its coordinates.
- **Actual:** Address lookup could not be enabled without a Google Maps API key and billing. The setup flow requested a one-time US$30 prepayment, so this demo uses manual coordinates.
- **Severity:** Major for address-based setup; manual entry remains available.
- **Workaround:** Enter latitude and longitude manually; the fields remain visible and editable.
- **Suggestion:** Make billing and prepayment requirements clear before API setup, and document a no-cost manual-coordinate path.
