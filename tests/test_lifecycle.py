import copy
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock, patch

import codex_assist as ai


def advice():
    return dict(title='Test', summary='Test', observations=[], limitations='Synthetic test',
                adjustments={**{k: 0 for k in ai.RANGES}, 'wb': 'camera'})


class AdvisorLifecycleTests(unittest.TestCase):
    def test_quality_filter_runs_before_publishing_the_advice(self):
        result_filter = Mock(side_effect=lambda source, base, result: {
            **result, 'settings': {**result['settings'], 'exposure': .4}, 'quality': {'strength': .5}})
        advisor = ai.Advisor(result_filter)
        advisor.jobs['job'] = dict(state='running', source_id='photo')
        def start(cmd, **kwargs):
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(advice()), encoding='utf8')
            return Mock(returncode=0)
        with patch.object(ai.subprocess, 'Popen', side_effect=start):
            advisor._run('job', ['codex'], b'a', b'b', {'wb': 'camera'}, 'goal', True)
        result_filter.assert_called_once()
        self.assertEqual(result_filter.call_args.args[0], 'photo')
        self.assertEqual(advisor.get('job')['state'], 'done')
        self.assertEqual(advisor.get('job')['result']['settings']['exposure'], .4)
        self.assertEqual(advisor.get('job')['result']['quality']['strength'], .5)

    def test_closed_photo_kills_running_process_and_never_publishes_late_result(self):
        advisor = ai.Advisor(); advisor.jobs['job'] = dict(state='running', source_id='photo')
        started, finish = threading.Event(), threading.Event()
        process = Mock(returncode=0)
        def start(cmd, **kwargs):
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(advice()), encoding='utf8')
            return process
        def communicate(*args, **kwargs):
            started.set(); finish.wait(3)
        process.communicate.side_effect = communicate
        process.kill.side_effect = finish.set
        with patch.object(ai.subprocess, 'Popen', side_effect=start):
            thread = threading.Thread(target=advisor._run, args=('job', ['codex'], b'a', b'b', {'wb': 'camera'}, 'goal', True))
            thread.start(); self.assertTrue(started.wait(2))
            advisor.cancel_source('photo'); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(advisor.get('job')['state'], 'cancelled')
        self.assertNotIn('result', advisor.get('job'))
        self.assertFalse(advisor.processes)
        process.kill.assert_called_once()

    def test_close_immediately_before_publish_is_atomic(self):
        advisor = ai.Advisor(); advisor.jobs['job'] = dict(state='running', source_id='photo')
        process = Mock(returncode=0)
        original = ai.validate_result
        def start(cmd, **kwargs):
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text(json.dumps(advice()), encoding='utf8')
            return process
        def validate(value):
            advisor.cancel_source('photo')
            return original(value)
        with patch.object(ai.subprocess, 'Popen', side_effect=start), patch.object(ai, 'validate_result', side_effect=validate):
            advisor._run('job', ['codex'], b'a', b'b', {'wb': 'camera'}, 'goal', True)
        self.assertEqual(advisor.get('job')['state'], 'cancelled')
        self.assertNotIn('result', advisor.get('job'))

    def test_shutdown_prevents_new_jobs_and_invalidates_completed_photo_results(self):
        advisor = ai.Advisor(); advisor.jobs['done'] = dict(state='done', source_id='photo', result={'settings': {}})
        advisor.shutdown()
        self.assertEqual(advisor.get('done')['state'], 'cancelled')
        self.assertNotIn('result', advisor.get('done'))
        with patch.object(ai, 'codex_command', return_value=['codex']):
            with self.assertRaises(ValueError):
                advisor.submit('new', b'', b'', {}, 'goal', True)


if __name__ == '__main__':
    unittest.main()
