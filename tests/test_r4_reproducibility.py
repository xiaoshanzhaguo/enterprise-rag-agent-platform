"""Legacy digest compatibility and non-destructive offline replay entrypoints."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from eval.r4.dataset import DIRECTORY, ROOT
from eval.r4.gold import load_gold, verify_files
from eval.r4.hybrid_experiments import evaluate, evaluation_output_dir


class ReproducibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='r4-repro-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.manifest=json.loads((DIRECTORY/'gold_v1_manifest.json').read_text(encoding='utf-8'))

    def copy_frozen(self,ending=b'\n',git_blobs=False):
        names=set(self.manifest['files_sha256'])|{'eval/r4/gold_v1_manifest.json','eval/week4_questions.jsonl'}
        for name in names:
            if git_blobs and name!='eval/r4/gold.py':
                data=subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),'show','HEAD:'+name],cwd=ROOT)
            else:
                data=(ROOT/name).read_bytes()
            target=self.root/name;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data.replace(b'\r\n',b'\n').replace(b'\n',ending))
        shutil.copyfile(DIRECTORY/'gold_v1_compatibility.json',self.root/'eval/r4/gold_v1_compatibility.json')
        return self.root/'eval/r4/gold_v1.json'

    def test_all_lf_checkout_loads_unchanged_gold(self):
        _,docs,cases=load_gold(self.copy_frozen(),root=self.root)
        self.assertEqual((len(docs),len(cases)),(12,38))

    def test_all_crlf_checkout_loads_unchanged_gold(self):
        _,docs,cases=load_gold(self.copy_frozen(b'\r\n'),root=self.root)
        self.assertEqual((len(docs),len(cases)),(12,38))

    def test_git_blob_checkout_in_fresh_python_process(self):
        self.copy_frozen(git_blobs=True)
        completed=subprocess.run([sys.executable,'-B','-c',
            'from eval.r4.gold import load_gold; print(len(load_gold()[2]))'],
            cwd=self.root,capture_output=True,text=True)
        self.assertEqual(completed.returncode,0,completed.stderr)
        self.assertEqual(completed.stdout.strip(),'38')

    def test_substantive_content_changes_are_still_rejected(self):
        gold=self.copy_frozen()
        source=self.root/'docs/sample_knowledge_base/hr/leave_policy.md'
        source.write_bytes(source.read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):load_gold(gold,root=self.root)

    def test_json_reserialization_is_not_a_newline_exception(self):
        gold=self.copy_frozen()
        data=json.loads(gold.read_text(encoding='utf-8'))
        gold.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):load_gold(gold,root=self.root)

    def test_unknown_expected_digest_is_not_accepted(self):
        self.copy_frozen()
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
            verify_files({'eval/r4/corpus.json':'0'*64},root=self.root)

    def test_only_declared_verifier_revision_is_accepted(self):
        self.copy_frozen()
        path=self.root/'eval/r4/gold.py'
        path.write_bytes(path.read_bytes()+b'\n# unapproved change\n')
        with self.assertRaisesRegex(ValueError,'SHA-256 mismatch'):
            verify_files({'eval/r4/gold.py':self.manifest['files_sha256']['eval/r4/gold.py']},root=self.root)

    def test_existing_reports_are_refused_before_any_evaluation(self):
        with self.assertRaisesRegex(ValueError,'new --output-dir'):evaluate()

    def test_replay_output_path_is_scoped_and_does_not_write_during_validation(self):
        allowed=ROOT/'eval/local/r4-replay-output-validation'
        before=allowed.exists()
        self.assertEqual(evaluation_output_dir(allowed),allowed.resolve())
        self.assertEqual(allowed.exists(),before)
        with self.assertRaises(ValueError):evaluation_output_dir(ROOT/'backend')

    def test_cli_exposes_independent_output_directory(self):
        result=subprocess.run([sys.executable,'-B','-m','eval.r4.hybrid_experiments','evaluate','--help'],
            cwd=ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('--output-dir',result.stdout)


if __name__=='__main__':unittest.main()
