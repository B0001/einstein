import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from einstein import cli
from einstein.arxiv_fetcher import ArxivFetchError
from einstein.arxiv_source import NoTexSourceError, Section, SourceExtraction
from einstein.cross_pollination import CrossPollinationCandidate, Match
from einstein.gaps import DEFAULT_PATENT_THRESHOLD, DEFAULT_REPO_THRESHOLD
from einstein.github_fetcher import GitHubFetchError, Issue
from einstein.openalex_fetcher import Edge, OpenAlexFetchError
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


def _openalex_paper(id_: str, title: str, summary: str, doi: str | None = None) -> Record:
    raw = {"doi": doi} if doi else {}
    return Record(
        type="paper", id=id_, title=title, summary=summary,
        url=f"https://openalex.org/{id_}", ts="2026-08-11T00:00:00+00:00", raw=raw,
    )


class _CliTestCase(unittest.TestCase):
    """Gives every CLI test its own throwaway `--db` path: the live default
    (`einstein.db` in cwd) would otherwise leak dedup/velocity state between
    tests and pollute the repo's working directory."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "test.db"

    def parse_args(self, args):
        return cli.build_parser().parse_args([*args, "--db", str(self.db_path)])


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
        self.assertEqual(args.db, cli.DEFAULT_DB_PATH)
        self.assertIsNone(args.cross_pollinate)
        self.assertIsNone(args.mine_constraints)
        self.assertEqual(args.constraint_similarity_threshold, cli.DEFAULT_CONSTRAINT_SIMILARITY_THRESHOLD)

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

    def test_db_and_cross_pollinate_overridable(self):
        args = cli.build_parser().parse_args([
            "photonics", "--db", "/tmp/x.db", "--cross-pollinate", "biology",
            "--cross-pollination-threshold", "0.42",
        ])
        self.assertEqual(args.db, "/tmp/x.db")
        self.assertEqual(args.cross_pollinate, "biology")
        self.assertEqual(args.cross_pollination_threshold, 0.42)

    def test_mine_constraints_repeatable_and_threshold_overridable(self):
        args = cli.build_parser().parse_args([
            "photonics", "--mine-constraints", "o/repo1", "--mine-constraints", "o/repo2",
            "--constraint-similarity-threshold", "0.42",
        ])
        self.assertEqual(args.mine_constraints, ["o/repo1", "o/repo2"])
        self.assertEqual(args.constraint_similarity_threshold, 0.42)


class RunTest(_CliTestCase):
    def test_fetches_all_three_sources_and_reports_counts(self):
        papers = [_paper("p1", "SGD convergence bounds", "convergence bounds for stochastic gradient descent")]
        repos = [_repo("o/sgd-bounds", "sgd-bounds", "convergence bounds for stochastic gradient descent")]
        args = self.parse_args(["optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        with patch.object(cli, "_fetch_papers", return_value=papers) as fp, \
             patch.object(cli, "_fetch_repos", return_value=repos) as fr, \
             patch.object(cli, "_fetch_patents", return_value=[]) as fpt:
            state, status, cross_pollination, _ = cli.run(args)

        fp.assert_called_once_with("optimization", max_results=cli.DEFAULT_MAX_RESULTS)
        fr.assert_called_once_with("optimization", max_results=cli.DEFAULT_MAX_RESULTS)
        fpt.assert_called_once_with("optimization", max_results=cli.DEFAULT_MAX_RESULTS)

        self.assertEqual(status["papers"], {"status": "fetched", "reason": None, "count": 1})
        self.assertEqual(status["repos"], {"status": "fetched", "reason": None, "count": 1})
        self.assertEqual(status["patents"], {"status": "fetched", "reason": None, "count": 0})
        self.assertEqual(state["gaps"], [])
        self.assertIsNone(cross_pollination)

    def test_skip_flags_never_call_the_real_fetcher(self):
        args = self.parse_args([
            "optimization", "--skip-papers", "--skip-repos", "--skip-patents",
        ])

        with patch.object(cli, "_fetch_papers") as fp, \
             patch.object(cli, "_fetch_repos") as fr, \
             patch.object(cli, "_fetch_patents") as fpt:
            state, status, _, _ = cli.run(args)

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
        args = self.parse_args(["optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]):
            state, _, _, _ = cli.run(args)

        gap_kinds = {g.subject_id: g.kind for g in state["gaps"]}
        self.assertEqual(gap_kinds, {"p2": "true_invention_gap", "o/k8s-tool": "unformalized_code"})
        self.assertEqual(len(state["agent_notes"]), 1)

    def test_live_fetch_failure_raises_source_fetch_error_not_empty_result(self):
        args = self.parse_args(["optimization"])

        with patch.object(cli, "_fetch_papers", side_effect=ArxivFetchError("boom", query="optimization", cause=Exception("boom"))), \
             patch.object(cli, "_fetch_repos", return_value=[]), \
             patch.object(cli, "_fetch_patents", return_value=[]):
            with self.assertRaises(cli.SourceFetchError) as ctx:
                cli.run(args)

        self.assertEqual(ctx.exception.source, "papers")
        self.assertIsInstance(ctx.exception.original, ArxivFetchError)

    def test_missing_uspto_key_is_a_fetch_error_not_a_silent_empty_patent_list(self):
        args = self.parse_args(["optimization"])

        with patch.object(cli, "_fetch_papers", return_value=[]), \
             patch.object(cli, "_fetch_repos", return_value=[]), \
             patch.object(cli, "_fetch_patents", side_effect=USPTOFetchError("no key", missing_key=True)):
            with self.assertRaises(cli.SourceFetchError) as ctx:
                cli.run(args)

        self.assertEqual(ctx.exception.source, "patents")
        self.assertTrue(ctx.exception.original.missing_key)

    def test_gaps_are_persisted_to_the_store_and_marked_new(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]
        args = self.parse_args(["optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]):
            state, _, _, _ = cli.run(args)

        self.assertEqual(len(state["scored_gaps"]), 2)
        self.assertTrue(all(sg.is_new for sg in state["scored_gaps"]))

        from einstein.store import Store
        with Store(self.db_path) as store:
            self.assertEqual(len(store.all_gaps()), 2)

    def test_second_run_against_the_same_db_sees_gaps_as_not_new(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]
        args = self.parse_args(["optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3"])

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]):
            cli.run(args)
            state, _, _, _ = cli.run(args)

        self.assertEqual(len(state["scored_gaps"]), 2)
        self.assertTrue(all(not sg.is_new for sg in state["scored_gaps"]))

    def test_cross_pollinate_fetches_both_corpora_and_persists_candidates(self):
        method_papers = [_openalex_paper("W1", "Method paper", "attention mechanism", doi="https://doi.org/10.1/m1")]
        domain_papers = [_openalex_paper("W2", "Domain paper", "protein folding", doi="https://doi.org/10.1/d1")]
        edges = [Edge(from_id="W1", to_id="W2")]
        args = self.parse_args([
            "optimization", "--skip-papers", "--skip-repos", "--skip-patents",
            "--cross-pollinate", "biology", "--cross-pollination-threshold", "0.0",
        ])

        def fake_search(query, **kwargs):
            return method_papers if query == "optimization" else domain_papers

        with patch.object(cli, "_search_papers", side_effect=fake_search) as fs, \
             patch.object(cli, "_fetch_citation_edges", return_value=edges) as fe, \
             patch.object(
                 cli,
                 "detect_cross_pollination",
                 return_value=[
                     CrossPollinationCandidate(
                         method_id="W1",
                         method_title="Method paper",
                         match=Match(against_type="paper", best_id="W2", best_similarity=0.9, threshold=0.0),
                     )
                 ],
             ):
            state, status, cross_pollination, _ = cli.run(args)

        self.assertEqual(fs.call_count, 2)
        self.assertEqual(fe.call_count, 2)  # one per unique DOI across both corpora
        self.assertIsNotNone(cross_pollination)
        self.assertEqual(cross_pollination["domain_b"], "biology")
        self.assertEqual(cross_pollination["method_corpus_size"], 1)
        self.assertEqual(cross_pollination["domain_corpus_size"], 1)
        self.assertEqual(cross_pollination["papers_without_doi"], 0)
        self.assertEqual(cross_pollination["edges_fetched"], 2)  # 1 edge per DOI call, 2 unique DOIs
        self.assertEqual(len(cross_pollination["candidates"]), 1)
        candidate = cross_pollination["candidates"][0]
        self.assertEqual(candidate["method_id"], "W1")
        self.assertTrue(candidate["velocity"]["is_new"])

        from einstein.store import Store
        with Store(self.db_path) as store:
            stored = store.all_gaps()
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["kind"], "cross_pollination")

    def test_cross_pollinate_papers_without_doi_are_counted_and_skipped(self):
        method_papers = [_openalex_paper("W1", "Method paper", "attention mechanism", doi=None)]
        domain_papers = [_openalex_paper("W2", "Domain paper", "protein folding", doi=None)]
        args = self.parse_args([
            "optimization", "--skip-papers", "--skip-repos", "--skip-patents",
            "--cross-pollinate", "biology",
        ])

        def fake_search(query, **kwargs):
            return method_papers if query == "optimization" else domain_papers

        with patch.object(cli, "_search_papers", side_effect=fake_search), \
             patch.object(cli, "_fetch_citation_edges") as fe, \
             patch.object(cli, "detect_cross_pollination", return_value=[]):
            state, status, cross_pollination, _ = cli.run(args)

        fe.assert_not_called()
        self.assertEqual(cross_pollination["papers_without_doi"], 2)
        self.assertEqual(cross_pollination["edges_fetched"], 0)

    def test_cross_pollinate_fetch_failure_raises_source_fetch_error(self):
        args = self.parse_args([
            "optimization", "--skip-papers", "--skip-repos", "--skip-patents",
            "--cross-pollinate", "biology",
        ])

        with patch.object(cli, "_search_papers", side_effect=OpenAlexFetchError(500, "boom")):
            with self.assertRaises(cli.SourceFetchError) as ctx:
                cli.run(args)

        self.assertEqual(ctx.exception.source, "cross_pollination")

    def test_cross_pollinate_not_requested_leaves_it_none(self):
        args = self.parse_args(["optimization", "--skip-papers", "--skip-repos", "--skip-patents"])

        with patch.object(cli, "_search_papers") as fs:
            _, _, cross_pollination, _ = cli.run(args)

        fs.assert_not_called()
        self.assertIsNone(cross_pollination)

    def test_mine_constraints_not_requested_leaves_it_none(self):
        args = self.parse_args(["optimization", "--skip-papers", "--skip-repos", "--skip-patents"])

        with patch.object(cli, "_fetch_issues") as fi, patch.object(cli, "_extract_source") as fx:
            _, _, _, constraint_mining = cli.run(args)

        fi.assert_not_called()
        fx.assert_not_called()
        self.assertIsNone(constraint_mining)

    def test_mine_constraints_unknown_repo_raises_source_fetch_error(self):
        repos = [_repo("o/known", "known", "a repo")]
        args = self.parse_args([
            "optimization", "--skip-papers", "--repo-threshold", "0.3", "--patent-threshold", "0.3",
            "--mine-constraints", "o/unknown",
        ])

        with patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch.object(cli, "_fetch_issues") as fi:
            with self.assertRaises(cli.SourceFetchError) as ctx:
                cli.run(args)

        self.assertEqual(ctx.exception.source, "constraint_mining")
        fi.assert_not_called()

    def test_mine_constraints_fetches_issues_and_paper_source_and_persists_widespread_cluster(self):
        papers = [_paper("2101.00001", "Paper A", "attention mechanism")]
        repos = [_repo("someone/lib", "lib", "a library")]
        extraction = SourceExtraction(
            arxiv_id="2101.00001", tex_filenames=("2101.00001.tex",), equations=(),
            limitations=(Section(heading="Limitations", text="cannot scale past 8192 tokens"),),
            future_work=(),
        )
        issue = Issue(
            repo="someone/lib", number=1, title="OOM above 8k context", body="runs out of memory",
            labels=("bug",), url="https://github.com/someone/lib/issues/1", ts="2026-08-08T00:00:00+00:00",
        )
        args = self.parse_args([
            "optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3",
            "--mine-constraints", "someone/lib", "--constraint-similarity-threshold", "0.0",
        ])

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch.object(cli, "_fetch_issues", return_value=[issue]) as fi, \
             patch.object(cli, "_extract_source", return_value=extraction) as fx:
            state, _, _, constraint_mining = cli.run(args)

        fi.assert_called_once_with("someone/lib", max_results=cli.DEFAULT_MAX_RESULTS)
        fx.assert_called_once_with("2101.00001")

        self.assertEqual(constraint_mining["repos"], ["someone/lib"])
        self.assertEqual(constraint_mining["papers_considered"], 1)
        self.assertEqual(constraint_mining["papers_without_tex_source"], 0)
        self.assertEqual(constraint_mining["pain_points_mined"], 2)
        self.assertEqual(constraint_mining["clusters"], 1)
        widespread = constraint_mining["widespread_clusters"]
        self.assertEqual(len(widespread), 1)
        self.assertEqual(sorted(widespread[0]["source_ids"]), ["2101.00001", "someone/lib"])
        self.assertTrue(widespread[0]["velocity"]["is_new"])

        from einstein.store import Store
        with Store(self.db_path) as store:
            stored = store.all_gaps()
            constraint_rows = [g for g in stored if g["kind"] == "constraint_cluster"]
            self.assertEqual(len(constraint_rows), 1)

    def test_mine_constraints_no_tex_source_is_counted_not_a_failure(self):
        papers = [_paper("2101.00002", "Paper B", "some text")]
        repos = [_repo("someone/lib", "lib", "a library")]
        args = self.parse_args([
            "optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3",
            "--mine-constraints", "someone/lib",
        ])

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch.object(cli, "_fetch_issues", return_value=[]), \
             patch.object(
                 cli, "_extract_source",
                 side_effect=NoTexSourceError("no tex", arxiv_id="2101.00002"),
             ):
            _, _, _, constraint_mining = cli.run(args)

        self.assertEqual(constraint_mining["papers_without_tex_source"], 1)
        self.assertEqual(constraint_mining["pain_points_mined"], 0)
        self.assertEqual(constraint_mining["widespread_clusters"], [])

    def test_mine_constraints_live_fetch_failure_raises_source_fetch_error(self):
        papers = [_paper("2101.00003", "Paper C", "some text")]
        repos = [_repo("someone/lib", "lib", "a library")]
        args = self.parse_args([
            "optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3",
            "--mine-constraints", "someone/lib",
        ])

        with patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch.object(cli, "_fetch_issues", side_effect=GitHubFetchError(500, "boom")):
            with self.assertRaises(cli.SourceFetchError) as ctx:
                cli.run(args)

        self.assertEqual(ctx.exception.source, "constraint_mining")


class MainTest(unittest.TestCase):
    def setUp(self):
        # cli.main builds its own parser, so the `--db` default must be
        # redirected here or these tests create `einstein.db` in cwd.
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        db_patch = patch.object(cli, "DEFAULT_DB_PATH", str(Path(tmpdir.name) / "default.db"))
        db_patch.start()
        self.addCleanup(db_patch.stop)

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

    def test_mine_constraints_report_renders_widespread_clusters(self):
        papers = [_paper("2101.00001", "Paper A", "attention mechanism")]
        repos = [_repo("someone/lib", "lib", "a library")]
        extraction = SourceExtraction(
            arxiv_id="2101.00001", tex_filenames=("2101.00001.tex",), equations=(),
            limitations=(Section(heading="Limitations", text="cannot scale past 8192 tokens"),),
            future_work=(),
        )
        issue = Issue(
            repo="someone/lib", number=1, title="OOM above 8k context", body="runs out of memory",
            labels=("bug",), url="https://github.com/someone/lib/issues/1", ts="2026-08-08T00:00:00+00:00",
        )
        out = io.StringIO()
        out.close = lambda: None

        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch.object(cli, "_fetch_issues", return_value=[issue]), \
             patch.object(cli, "_extract_source", return_value=extraction), \
             patch("sys.stdout", out):
            code = cli.main([
                "optimization", "--repo-threshold", "0.3", "--patent-threshold", "0.3",
                "--mine-constraints", "someone/lib", "--constraint-similarity-threshold", "0.0",
                "--db", str(Path(tmp) / "test.db"),
            ])

        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertIn("constraint_mining (repos=['someone/lib'])", text)
        self.assertIn("widespread clusters: 1", text)

        out_json = io.StringIO()
        out_json.close = lambda: None
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(cli, "_fetch_papers", return_value=papers), \
             patch.object(cli, "_fetch_repos", return_value=repos), \
             patch.object(cli, "_fetch_patents", return_value=[]), \
             patch.object(cli, "_fetch_issues", return_value=[issue]), \
             patch.object(cli, "_extract_source", return_value=extraction), \
             patch("sys.stdout", out_json):
            code = cli.main([
                "optimization", "--json", "--repo-threshold", "0.3", "--patent-threshold", "0.3",
                "--mine-constraints", "someone/lib", "--constraint-similarity-threshold", "0.0",
                "--db", str(Path(tmp) / "test.db"),
            ])

        self.assertEqual(code, 0)
        report = json.loads(out_json.getvalue())
        self.assertIn("constraint_mining", report)
        self.assertEqual(len(report["constraint_mining"]["widespread_clusters"]), 1)


if __name__ == "__main__":
    unittest.main()
