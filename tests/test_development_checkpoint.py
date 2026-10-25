import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from tools.development_checkpoint import atomic_json,quota_summary


class DevelopmentCheckpointTests(unittest.TestCase):
    def test_missing_stale_and_invalid_usage_is_never_guessed(self):
        now=dt.datetime(2026,10,25,tzinfo=dt.timezone.utc)
        self.assertEqual(quota_summary(None,now)["windows"],[])
        for used in (True,float("nan"),-1,101,"5",None):
            result=quota_summary(dict(observed_at=now.isoformat(),rateLimits=dict(primary=dict(usedPercent=used))),now)
            self.assertEqual(result["windows"],[])
            self.assertEqual(result["decision"],"checkpoint_by_stage")
        old=(now-dt.timedelta(hours=1)).isoformat()
        self.assertEqual(quota_summary(dict(observed_at=old,rateLimits=dict(primary=dict(usedPercent=99))),now)["source"],"stale_or_invalid_snapshot")

    def test_low_either_window_pauses_and_account_secrets_are_not_copied(self):
        now=dt.datetime.now(dt.timezone.utc)
        payload=dict(observed_at=now.isoformat(),accountId="private-account",api_key="do-not-copy",rateLimits=dict(primary=dict(usedPercent=20,windowDurationMins=300,resetsAt=1792926776),secondary=dict(usedPercent=91,windowDurationMins=10080)))
        result=quota_summary(payload,now)
        self.assertEqual(result["decision"],"finish_current_and_pause")
        self.assertEqual(result["windows"][1]["remaining_percent"],9)
        self.assertNotIn("private-account",json.dumps(result));self.assertNotIn("do-not-copy",json.dumps(result))
        payload["rateLimits"]["secondary"]["usedPercent"]=4
        self.assertEqual(quota_summary(payload,now)["decision"],"may_start_next_stage")
        payload["ordinaryUsageAllowed"]=False
        self.assertEqual(quota_summary(payload,now)["decision"],"finish_current_and_pause")

    def test_atomic_replacement_is_readable_and_leaves_no_part_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"checkpoint.json"
            atomic_json(path,dict(version=1,stage="原生界面"));atomic_json(path,dict(version=2))
            self.assertEqual(json.loads(path.read_text(encoding="utf8")),dict(version=2))
            self.assertEqual(list(Path(directory).glob("*.part")),[])
