"""Isolated comparator/validation tests; these fixtures are not product records or live API proof."""
import os, uuid, unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
os.environ.setdefault("WEATHER_USER_AGENT", "FieldCallUnitTests/0.1 (contact: test@example.com)")
os.environ.setdefault("GOOGLE_CLIENT_ID", "unit-test-client.apps.googleusercontent.com")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "unit-test-secret")
os.environ.setdefault("GOOGLE_REDIRECT_URI", "http://localhost:8080/auth/callback")
os.environ.setdefault("SESSION_SIGNING_KEY", "unit-test-signing-key-" + "x" * 40)
os.environ["DATABASE_PATH"] = str(Path(__file__).resolve().parent.parent / "data" / ("unit-" + uuid.uuid4().hex) / "fieldcall.sqlite3")
import app

class FieldCallLogicTests(unittest.TestCase):
    def event(self):
        start=datetime.now(timezone.utc)+timedelta(days=1); end=start+timedelta(hours=2)
        return {"name":"Unit-only input","latitude":6.45,"longitude":3.39,"starts_at":start.isoformat(),"ends_at":end.isoformat(),"max_temp_c":30,"min_temp_c":None,"max_wind_kph":25,"max_precip_mm":5}
    def period(self,start,hours=1,temperature=20,wind_ms=5,precip_mm=2):
        return {"start":start.isoformat(),"hours":hours,"temperature":temperature,"wind_ms":wind_ms,"precip_mm":precip_mm}
    def test_event_validation_requires_timezone_and_threshold(self):
        x=self.event();x["starts_at"]="2030-01-01T10:00"
        with self.assertRaises(ValueError):app.validate_event(x)
        x=self.event();x.update(max_temp_c=None,max_wind_kph=None,max_precip_mm=None)
        with self.assertRaises(ValueError):app.validate_event(x)
    def test_event_validation_checks_ranges_and_window(self):
        x=self.event();x["latitude"]=91
        with self.assertRaises(ValueError):app.validate_event(x)
        x=self.event();x["ends_at"]=(datetime.fromisoformat(x["starts_at"])+timedelta(days=4)).isoformat()
        with self.assertRaises(ValueError):app.validate_event(x)
    def test_normalize_met_documented_global_fields_and_missing_values(self):
        doc={"properties":{"meta":{"updated_at":"2026-10-13T00:00:00Z"},"timeseries":[
            {"time":"2026-10-14T00:00:00Z","data":{"instant":{"details":{"air_temperature":22,"wind_speed":4}},"next_1_hours":{"details":{"precipitation_amount":1.5}}}},
            {"time":"2026-10-14T01:00:00Z","data":{"instant":{"details":{"air_temperature":None,"wind_speed":None}}}}]}}
        periods,updated=app.normalize_forecast(doc)
        self.assertEqual(updated,"2026-10-13T00:00:00Z");self.assertEqual(periods[0]["precip_mm"],1.5);self.assertIsNone(periods[1]["temperature"])
    def test_compare_handles_global_units_and_rain_amount(self):
        e=self.event();s=datetime.fromisoformat(e["starts_at"]);end=datetime.fromisoformat(e["ends_at"])
        result,metrics,_=app.compare(e,[self.period(s,temperature=22,wind_ms=5,precip_mm=4),self.period(s+timedelta(hours=1),temperature=23,wind_ms=6,precip_mm=3)])
        self.assertEqual(result,"within your limits");self.assertAlmostEqual(metrics["max_wind_kph"],21.6);self.assertEqual(metrics["max_precip_mm"],4)
    def test_exceedance_and_missing_required_measurement(self):
        e=self.event();e["max_temp_c"]=18;s=datetime.fromisoformat(e["starts_at"])
        result,_,_=app.compare(e,[self.period(s),self.period(s+timedelta(hours=1))]);self.assertEqual(result,"outside your limits")
        e["max_temp_c"]=None;e["max_precip_mm"]=5
        result,_,_=app.compare(e,[self.period(s,precip_mm=None),self.period(s+timedelta(hours=1),precip_mm=None)]);self.assertEqual(result,"unknown")
    def test_partial_coverage_cannot_be_within_limits(self):
        e=self.event();s=datetime.fromisoformat(e["starts_at"])
        result,_,_=app.compare(e,[self.period(s),self.period(s+timedelta(hours=2))]);self.assertEqual(result,"unknown")
    def test_partial_overlap_rain_aggregate_is_not_attributed_to_event(self):
        e=self.event();s=datetime.fromisoformat(e["starts_at"])
        result,metrics,_=app.compare(e,[self.period(s-timedelta(minutes=30),hours=1,precip_mm=40),self.period(s+timedelta(minutes=30),hours=1,precip_mm=0),self.period(s+timedelta(hours=1,minutes=30),hours=1,precip_mm=0)])
        self.assertEqual(result,"unknown")
        self.assertEqual(metrics["max_precip_mm"],0)
    def test_empty_overlap_is_unknown(self):
        e=self.event();s=datetime.fromisoformat(e["starts_at"])-timedelta(days=4)
        result,_,_=app.compare(e,[self.period(s)]);self.assertEqual(result,"unknown")
    def test_signed_session_round_trip_and_tampering(self):
        user={"sub":"google-subject-a","email":"a@example.test","name":"A"}
        token=app.session_token(user)
        self.assertEqual(app.decode_session(token),user)
        self.assertIsNone(app.decode_session(token+"x"))
    def test_saved_events_are_partitioned_by_google_subject(self):
        owner=app.CURRENT_USER.set({"sub":"google-subject-a","email":"a@example.test"})
        try:
            saved=app.save_outdoor_event(**self.event())
            self.assertNotIn("owner_id",saved)
            self.assertEqual([x["id"] for x in app.list_outdoor_events()],[saved["id"]])
        finally:
            app.CURRENT_USER.reset(owner)
        other=app.CURRENT_USER.set({"sub":"google-subject-b","email":"b@example.test"})
        try:
            self.assertEqual(app.list_outdoor_events(),[])
        finally:
            app.CURRENT_USER.reset(other)

if __name__=="__main__":unittest.main()
