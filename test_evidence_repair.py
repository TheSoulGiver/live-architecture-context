"""Broken source anchors stay failures until an explicit, unique repair is applied."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import archctx


TOOL = Path(__file__).with_name('archctx.py')


class EvidenceRepairTest(unittest.TestCase):
    def fixture(self, root, text, contains):
        (root / 'owner.py').write_text(text, encoding='utf-8')
        config = root / 'architecture.json'
        archctx.atomic(config, {'version': 1, 'repo': '.', 'components': [
            {'id': 'owner', 'truth_sources': ['owner.py'], 'evidence': [
                {'path': 'owner.py', 'contains': contains}]}], 'relations': []})
        return config

    def cli(self, config, *args):
        result = subprocess.run([sys.executable, str(TOOL), '--config', str(config), *args],
                                text=True, encoding='utf-8', capture_output=True)
        return result.returncode, json.loads(result.stdout)

    def test_reports_every_broken_anchor_with_original_and_nearest_line(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.fixture(root, 'def execute_current_task():\n    return pending_jobs\n', 'def execute_current_tasks():')
            value = archctx.load(config)
            value['components'][0]['evidence'].append({'path': 'owner.py', 'contains': 'return pending_work'})
            archctx.atomic(config, value)
            self.assertEqual(len(archctx.validate(root, value)[2]), 2)
            diagnostics = archctx.evidence_diagnostics(root, value, config)
            self.assertEqual(len(diagnostics), 2)
            first = diagnostics[0]
            self.assertEqual((first['component'], first['path'], first['contains']),
                             ('owner', 'owner.py', 'def execute_current_tasks():'))
            self.assertEqual(first['nearest_line']['text'], 'def execute_current_task():')
            self.assertEqual(first['nearest_line']['line'], 1)
            self.assertEqual(first['repair']['contains'], 'def execute_current_task():')
            self.assertIn('evidence-repair', first['repair_command'])
            self.assertEqual(len(archctx.validate(root, archctx.load(config))[2]), 2)

    def test_cli_repairs_only_selected_anchor_and_preserves_last_good_and_truth_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.fixture(root, 'def execute_current_task():\n', 'def execute_current_tasks():')
            before = archctx.load(config)
            before['components'][0]['evidence'].append({'path': 'owner.py', 'contains': 'a claim with no current source'})
            archctx.atomic(config, before)
            last_good = root / '.archctx/last-good.json'
            archctx.atomic(last_good, {'old': 'accepted evidence'})
            accepted_bytes = last_good.read_bytes()
            code, result = self.cli(config, 'evidence-diagnose')
            self.assertEqual(code, 2)
            selected = result['evidence_diagnostics'][0]
            code, repaired = self.cli(config, 'evidence-repair', '--id', selected['id'])
            self.assertEqual((code, repaired['status']), (0, 'REPAIRED'))
            current = archctx.load(config)
            self.assertEqual(current['components'][0]['truth_sources'], ['owner.py'])
            self.assertEqual(current['components'][0]['evidence'][0]['contains'], 'def execute_current_task():')
            self.assertEqual(current['components'][0]['evidence'][1], before['components'][0]['evidence'][1])
            self.assertEqual(len(archctx.validate(root, current)[2]), 1)
            self.assertEqual(last_good.read_bytes(), accepted_bytes)

    def test_unique_code_move_is_found_in_tracked_source_and_fixed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.fixture(root, '# implementation moved\n', 'def execute_current_task():')
            (root / 'moved.py').write_text('def execute_current_task():\n    pass\n', encoding='utf-8')
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'add', 'owner.py', 'moved.py'], check=True, capture_output=True)
            diagnostics = archctx.evidence_diagnostics(root, archctx.load(config), config)
            self.assertEqual(diagnostics[0]['repair']['path'], 'moved.py')
            self.assertEqual(diagnostics[0]['repair']['kind'], 'exact_move')
            code, _ = self.cli(config, 'evidence-repair', '--id', diagnostics[0]['id'])
            self.assertEqual(code, 0)
            self.assertEqual(archctx.validate(root, archctx.load(config))[2], [])

    def test_ambiguous_nearby_lines_and_duplicate_moves_never_choose(self):
        for source in ('def execute_current_tasks_a():\ndef execute_current_tasks_b():\n',
                       'def execute_current_tasks():\ndef execute_current_tasks():\n'):
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = self.fixture(root, source, 'def execute_current_tasks_old():')
                if 'tasks_a' not in source:
                    value = archctx.load(config)
                    value['components'][0]['evidence'][0]['path'] = 'old.py'
                    value['components'][0]['evidence'][0]['contains'] = 'def execute_current_tasks():'
                    archctx.atomic(config, value)
                diagnostic = archctx.evidence_diagnostics(root, archctx.load(config), config)[0]
                self.assertIsNone(diagnostic['repair'])
                self.assertIsNone(diagnostic['repair_command'])
                self.assertEqual(diagnostic['repair_state'], 'ambiguous')

    def test_stale_repair_command_refuses_to_apply_a_new_suggestion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.fixture(root, 'def execute_current_task():\n', 'def execute_current_tasks():')
            diagnostic = archctx.evidence_diagnostics(root, archctx.load(config), config)[0]
            before = config.read_bytes()
            (root / 'owner.py').write_text('def execute_current_task2():\n', encoding='utf-8')
            code, result = self.cli(config, 'evidence-repair', '--id', diagnostic['id'])
            self.assertEqual((code, result['status']), (2, 'ERROR'))
            self.assertIn('stale', result['error'])
            self.assertEqual(config.read_bytes(), before)

    def test_local_change_and_exact_copy_elsewhere_are_ambiguous(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.fixture(root, 'def execute_current_task():\n', 'def execute_current_tasks():')
            (root / 'copy.py').write_text('def execute_current_tasks():\n', encoding='utf-8')
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            subprocess.run(['git', '-C', str(root), 'add', 'owner.py', 'copy.py'], check=True, capture_output=True)
            diagnostic = archctx.evidence_diagnostics(root, archctx.load(config), config)[0]
            self.assertIsNone(diagnostic['repair'])
            self.assertEqual(diagnostic['repair_state'], 'ambiguous')

    def test_relation_failure_reports_its_owner_without_losing_component_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = self.fixture(root, 'def execute_current_task():\n', 'def execute_current_task')
            value = archctx.load(config)
            value['relations'] = [{'from': 'owner', 'to': 'owner', 'kind': 'schedules', 'evidence': [
                {'path': 'owner.py', 'contains': 'def execute_current_tasks():'}]}]
            diagnostic = archctx.evidence_diagnostics(root, value, config)[0]
            self.assertEqual((diagnostic['owner_type'], diagnostic['owner'], diagnostic['component']),
                             ('relation', 'owner--schedules--owner', 'owner'))


if __name__ == '__main__':
    unittest.main()
