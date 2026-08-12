import io
import json
import unittest
from unittest.mock import patch

from einstein import cli
from einstein.arxiv_fetcher import ArxivFetchError
from einstein.gaps import DEFAULT_PATENT_THRESHOLD, DEFAULT_REPO_THRESHOLD
from einstein.schema import Record
from einstein.uspto_fetcher import USPTOFetchError


def _paper(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="paper", id=id_, title=title, summary=summary,
        url=f"https://arxiv.org/abs/{id_}", ts="2026-08-11T00:00:00+00:00", raw={},
    )


def _repo(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="repo", id=id_, title=title, summary=summary,
        url=f"https://github.com/{id_}", ts="2026-08-11T00:00:00+00:00", raw={},
    )


class BuildParserTest(unittest.TestCase):
    def test_defaults_match_gaps_module(self):
        args = cli.build_parser().parse_args(["photonics"])
        self.assertEqual(args.domain, "photonics")
        self.assertEqual(args.repo_threshold, DEFAULT_REPO_THRESHOLD)
        self.assertEqual(args.patent_threshold, DEFAULT_PATENT_THRESHOLD)
        self.assertFalse(args.skip_papers)
        self.assertFalse(args.skip_repos)
        self.assertFalse(args.skip_patents)
        self.assertFalse(args.as_json)

    def test_thresholds_and_skip_flags_overridable(self):
        args = cli.build_parser().parse_args([
            "photonics", "--repo-threshold", "0.5", "--patent-threshold", "0.6",
            "--skip-papers", "--skip-patents", "--json",
        ])
        self.assertEqual(args.repo_threshold, 0.5)
        self.assertEqual(args.patent_threshold, 0.6)
        self.assertTrue(args.skip_papers)
        self.assertFalse(args.skip_repos)
        self.assertTrue(args.skip_patents)
        self.assertTrue(args.as_json)


class RunTest(unittest.TestCase):
    def test_fetches_all_three_sources_and_reports_counts(self):
        papers = [_paper("p1", "SGD convergence bounds", "convergence bounds for stochastic gradient descent")]
        repos = [_repo("o/sgd-bounds", "sgd-bounds", "convergence bounds for stochastic gradient descent")]
        args = cli.build_parser().parse_args(["optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        with patch.object(cli, "_fetch_papers", return_value=papers) as fp, \
             patch.object(cli, "_fetch_repos", return_value=repos) as fr, \
             patch.object(cli, "_fetch_patents", return_value=[]) as fpt:
            state, status = cli.run(args)

        fp.assert_called_once_with("optimization", max_results=cli.DEFAULT_MAX_RESULTS)
        fr.assert_called_once_with("optimization", max_results=cli.DEFAULT_MAX_RESULTS)
        fpt.assert_called_once_with("optimization", max_results=cli.DEFAULT_MAX_RESULTS)

        self.assertEqual(status["papers"], {"status": "fetched", "reason": None, "count": 1})
        self.assertEqual(status["repos"], {"status": "fetched", "reason": None, "count": 1})
        self.assertEqual(status["patents"], {"status": "fetched", "reason": None, "count": 0})
        self.assertEqual(state["gaps"], [])

    def test_skip_flags_never_call_the_real_fetcher(self):
        args = cli.build_parser().parse_args([
            "optimization", "--skip-papers", "--skip-repos", "--skip-patents",
        ])

        with patch.object(cli, "_fetch_papers") as fp, \
             patch.object(cli, "_fetch_repos") as fr, \
             patch.object(cli, "_fetch_patents") as fpt:
            state, status = cli.run(args)

        fp.assert_not_called()
        fr.assert_not_called()
        fpt.assert_not_called()
        self.assertEqual(status["papers"], {"status": "skipped", "reason": "--skip-papers", "count": 0})
        self.assertEqual(status["repos"], {"status": "skipped", "reason": "--skip-repos", "count": 0})
        self.assertEqual(status["patents"], {"status": "skipped", "reason": "--skip-patents", "count": 0})
        self.assertEqual(state["papers"], [])
        self.assertEqual(state["repos"], [])
        self.assertEqual(state["patents"], [])

    def test_detects_a_true_invention_gap_and_routes_through_agents_stub(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]
        args = cli.build_parser().parse_args(["optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]):
            state, _ = cli.run(args)

        gap_kinds = {g.subject_id: g.kind for g in state["gaps"]}
        self.assertEqual(gap_kinds, {"p2": "true_invention_gap", "o/k8s-tool": "unformalized_code"})
        self.assertEqual(len(state["agent_notes"]), 1)

    def test_live_fetch_failure_raises_source_fetch_error_not_empty_result(self):
        args = cli.build_parser().parse_args(["optimization"])

        with patch.object(cli, "_fetch_papers", side_effect=ArxivFetchError("boom", query="optimization", cause=Exception("boom"))), \
             patch.object(cli, "_fetch_repos", return_value=[]), \
             patch.object(cli, "_fetch_patents", return_value=[]):
            with self.assertRaises(cli.SourceFetchError) as ctx:
                cli.run(args)

        self.assertEqual(ctx.exception.source, "papers")
        self.assertIsInstance(ctx.exception.original, ArxivFetchError)

    def test_missing_uspto_key_is_a_fetch_error_not_a_silent_empty_patent_list(self):
        args = cli.build_parser().parse_args(["optimization"])

        with patch.object(cli, "_fetch_papers", return_value=[]), \
             patch.object(cli, "_fetch_repos", return_value=[]), \
             patch.object(cli, "_fetch_patents", side_effect=USPTOFetchError("no key", missing_key=True)):
            with self.assertRaises(cli.SourceFetchError) as ctx:
                cli.run(args)

        self.assertEqual(ctx.exception.source, "patents")
        self.assertTrue(ctx.exception.original.missing_key)


class MainTest(unittest.TestCase):
    def test_text_report_success_exit_zero(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]
        out = io.StringIO()
        out.close = lambda: None  # argparse.FileType would close a real file; StringIO must survive to be read

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch("sys.stdout", out):
            code = cli.main(["optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("domain: optimization", text)
        self.assertIn("papers: fetched 1 record(s)", text)
        self.assertIn("true_invention_gap", text)
        self.assertIn("unformalized_code", text)

    def test_json_report_is_valid_json_with_gap_and_source_detail(self):
        papers = [_paper("p1", "SGD convergence bounds", "convergence bounds for stochastic gradient descent")]
        out = io.StringIO()
        out.close = lambda: None

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=[]), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch("sys.stdout", out):
            code = cli.main(["optimization", "--json", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["domain"], "optimization")
        self.assertEqual(report["sources"]["repos"], {"status": "fetched", "reason": None, "count": 0})
        self.assertEqual(len(report["gaps"]), 1)
        self.assertEqual(report["gaps"][0]["kind"], "true_invention_gap")
        self.assertEqual(report["gap_counts"], {"true_invention_gap": 1})

    def test_skip_flags_produce_report_with_no_network_calls(self):
        out = io.StringIO()
        out.close = lambda: None

        with patch.object(cli, "_fetch_papers") as fp, \
             patch.object(cli, "_fetch_repos") as fr, \
             patch.object(cli, "_fetch_patents") as fpt, \
             patch("sys.stdout", out):
            code = cli.main(["optimization", "--skip-papers", "--skip-repos", "--skip-patents"])

        self.assertEqual(code, 0)
        fp.assert_not_called()
        fr.assert_not_called()
        fpt.assert_not_called()
        text = out.getvalue()
        self.assertIn("papers: skipped (--skip-papers)", text)
        self.assertIn("repos: skipped (--skip-repos)", text)
        self.assertIn("patents: skipped (--skip-patents)", text)

    def test_live_fetch_failure_exits_nonzero_and_writes_no_report(self):
        out = io.StringIO()
        out.close = lambda: None
        err = io.StringIO()

        with patch.object(cli, "_fetch_papers", side_effect=ArxivFetchError("boom", query="optimization", cause=Exception("boom"))), \
             patch.object(cli, "_fetch_repos", return_value=[]), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch("sys.stdout", out):
            code = cli.main(["optimization"], stderr=err)

        self.assertEqual(code, 1)
        self.assertEqual(out.getvalue(), "", "a failed fetch must not produce a partial report on stdout")
        self.assertIn("papers fetch failed", err.getvalue())

    def test_missing_uspto_key_error_message_hints_at_skip_flag(self):
        out = io.StringIO()
        out.close = lambda: None
        err = io.StringIO()

        with patch.object(cli, "_fetch_papers", return_value=[]), \
             patch.object(cli, "_fetch_repos", return_value=[]), \
             patch.object(cli, "_fetch_patents", side_effect=USPTOFetchError("no key", missing_key=True)), \
             patch("sys.stdout", out):
            code = cli.main(["optimization"], stderr=err)

        self.assertEqual(code, 1)
        self.assertIn("--skip-patents", err.getvalue())


if __name__ == "__main__":
    unittest.main()
