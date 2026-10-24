"""Local contract tests; no network requests, no Codex quota used."""
import copy
import json
from pathlib import Path
import unittest
import subprocess
from unittest.mock import Mock, patch

import codex_assist as ai


class AdviceContractTests(unittest.TestCase):
    def setUp(self):
        self.good = {'title': '自然明亮', 'summary': '保留质感并改善暗部。',
                     'observations': ['画面暗部较多。'], 'limitations': '不保证恢复丢失细节。',
                     'adjustments': {**{k: 0 for k in ai.RANGES}, 'wb': 'camera'}}

    def test_legal_response(self):
        self.assertEqual(ai.validate_result(self.good), self.good)

    def test_illegal_values_rejected(self):
        for value in (float('nan'), float('inf'), True, '1', 99):
            with self.subTest(value=repr(value)):
                data = copy.deepcopy(self.good)
                data['adjustments']['exposure'] = value
                with self.assertRaises(ValueError):
                    ai.validate_result(data)

    def test_unknown_model_fields_cannot_be_applied(self):
        for key in ('rotation', 'crop', 'command', '__proto__'):
            data = copy.deepcopy(self.good)
            data['adjustments'][key] = 'untrusted'
            with self.assertRaises(ValueError):
                ai.validate_result(data)

    def test_schema_requires_complete_bounded_parameters(self):
        data = copy.deepcopy(self.good)
        del data['adjustments']['contrast']
        with self.assertRaises(ValueError):
            ai.validate_result(data)
        data = copy.deepcopy(self.good)
        data['adjustments']['wb'] = 'execute'
        with self.assertRaises(ValueError):
            ai.validate_result(data)

    def test_prompt_treats_user_text_as_json_data(self):
        goal = '自然调色\n"; remove-files; & $(test)'
        prompt = ai.build_prompt({'rotation': 2}, goal, True)
        self.assertIn(json.dumps(goal, ensure_ascii=False), prompt)
        self.assertIn('不使用任何工具', prompt)

    def test_cli_is_restricted_and_prompt_uses_stdin(self):
        folder = Path('example')
        cmd = ai.make_command(['codex.exe'], folder, [folder / 'a.jpg', folder / 'b.jpg'])
        self.assertIn('--ignore-user-config', cmd)
        self.assertIn('--ephemeral', cmd)
        self.assertEqual(cmd[cmd.index('--sandbox') + 1], 'read-only')
        self.assertIn('shell_tool', cmd)
        self.assertIn('--output-schema', cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', cmd)
        self.assertEqual(cmd[-1], '-')

    def test_missing_cli_is_an_explicit_error(self):
        with patch.object(ai, 'codex_command', return_value=None):
            self.assertFalse(ai.status()['ready'])
            with self.assertRaises(ValueError):
                ai.Advisor().submit('source', b'', b'', {}, '自然', True)

    def test_second_job_cannot_spend_quota_while_busy(self):
        advisor = ai.Advisor()
        advisor.jobs['first'] = {'state': 'running'}
        with patch.object(ai, 'codex_command', return_value=['codex.exe']):
            with self.assertRaises(ValueError):
                advisor.submit('source', b'', b'', {}, '自然', True)

    def test_worker_preserves_composition_and_manual_wb(self):
        advisor = ai.Advisor()
        advisor.jobs['job'] = {'state': 'running'}
        p = {**{k: 0 for k in ai.RANGES}, 'wb': 'daylight', 'crop': '1:1', 'rotation': 1}
        process = Mock(returncode=0)
        def start(cmd, **kwargs):
            path = Path(cmd[cmd.index('--output-last-message') + 1])
            path.write_text(json.dumps(self.good), encoding='utf8')
            return process
        with patch.object(ai.subprocess, 'Popen', side_effect=start):
            advisor._run('job', ['codex.exe'], b'before', b'current', p, '自然', True)
        result = advisor.get('job')
        self.assertEqual(result['state'], 'done')
        self.assertEqual(result['result']['settings']['rotation'], 1)
        self.assertEqual(result['result']['settings']['crop'], '1:1')
        self.assertEqual(result['result']['settings']['wb'], 'daylight')

    def test_timeout_kills_process_and_returns_error(self):
        advisor = ai.Advisor()
        advisor.jobs['job'] = {'state': 'running'}
        process = Mock(returncode=None)
        process.communicate.side_effect = [subprocess.TimeoutExpired('codex', 180), (None, None)]
        with patch.object(ai.subprocess, 'Popen', return_value=process):
            advisor._run('job', ['codex.exe'], b'before', b'current', {}, '自然', True)
        self.assertEqual(advisor.get('job')['state'], 'error')
        process.kill.assert_called_once()

    def test_invalid_model_response_never_becomes_recipe(self):
        advisor = ai.Advisor()
        advisor.jobs['job'] = {'state': 'running'}
        process = Mock(returncode=0)
        def start(cmd, **kwargs):
            Path(cmd[cmd.index('--output-last-message') + 1]).write_text('{"command":"bad"}', encoding='utf8')
            return process
        with patch.object(ai.subprocess, 'Popen', side_effect=start):
            advisor._run('job', ['codex.exe'], b'before', b'current', {}, '自然', True)
        result = advisor.get('job')
        self.assertEqual(result['state'], 'error')
        self.assertNotIn('result', result)


if __name__ == '__main__':
    unittest.main()
