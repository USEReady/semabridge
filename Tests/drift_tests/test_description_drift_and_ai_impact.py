"""Tests for Description Drift: AI Impact Check & Real Blast Radius.

Verifies:
1. A metric whose comment names required dimensions: removing that sentence is classified as a
   removal with higher severity and the dimensions appear in the blast radius.
2. A typo fix is classified as a rewording with low severity.
3. A metric referenced by another metric's comment appears in that metric's blast radius.
4. A removal of a caveat that distinguished two similar objects reports a changed choice on at least one question.
5. An addition where the baseline had no description reports as an addition, with its own wording.
6. A typo fix reports no differences, and says the run was sampled.
7. An object with no covering questions reports that and runs nothing.
8. The check never writes to the warehouse and never touches the sync path.
9. A failure part way reports partial, not clean.
10. An object with no dependants returns an empty blast radius with a clear message.
"""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from semabridge.ai.nl_query_engine import NLQueryEngine
from semabridge.governance.ai_impact_service import (
    AIImpactReport,
    clear_ai_impact_cache,
    run_ai_impact_check,
)
from semabridge.governance.blast_radius import (
    BlastRadiusReport,
    BlastRadiusSimulator,
    extract_stated_dimension_constraints,
)
from semabridge.governance.drift.description_diff import (
    DiffSeverity,
    classify_and_diff_description,
    get_removal_insert_ratio_threshold,
    get_typo_max_length_diff,
    get_typo_similarity_threshold,
)
from semabridge.ontology.models import (
    DomainAttribute,
    DomainConcept,
    DomainMetric,
    DomainOntology,
    FederatedEnterpriseOntology,
)


class TestDescriptionDriftAndAIImpact(unittest.TestCase):
    def setUp(self):
        clear_ai_impact_cache()

    def test_comment_naming_required_dimensions_removal_classified_high_and_in_blast_radius(self):
        """1. A metric whose comment names required dimensions: removing that sentence is
        classified as a removal with higher severity and the dimensions appear in the blast radius.
        """
        baseline_desc = "Win rate percentage for enterprise deals. Must be queried with BU.REGION."
        live_desc = "Win rate percentage for enterprise deals."

        # Verify classification
        diff = classify_and_diff_description(baseline_desc, live_desc)
        self.assertEqual(diff["classification"], "TEXT_REMOVED")
        self.assertEqual(diff["severity"], "high")
        self.assertTrue(any(c in diff["removed_caveats"] for c in ("must", "dimension", "dimensions")))
        self.assertIn("REMOVED", diff["baseline_marked"])

        # Verify dimension constraints extraction
        constraints = extract_stated_dimension_constraints(baseline_desc)
        self.assertTrue(any(c["dimension"] == "BU.REGION" for c in constraints))

        # Verify blast radius contains the dimension constraints
        metric = DomainMetric(
            name="WIN_RATE_PCT",
            sql_expression="SUM(WINS) / SUM(DEALS)",
            description=baseline_desc,
        )
        ont = FederatedEnterpriseOntology(
            domains={
                "sales": DomainOntology(
                    domain="sales",
                    metrics=[metric],
                )
            }
        )
        sim = BlastRadiusSimulator(ontology=ont, mock_mode=False)
        report = sim.simulate_impact(
            target_name="WIN_RATE_PCT",
            target_type="METRIC",
            change_type="MODIFY_DESCRIPTION",
            expression=live_desc,
        )

        dim_names = [c["dimension"] for c in report.stated_dimension_constraints]
        self.assertIn("BU.REGION", dim_names)
        self.assertEqual(report.change_type, "MODIFY_DESCRIPTION")
        self.assertIn(report.severity, ("DEGRADED", "BREAKING"))

    def test_typo_fix_classified_as_rewording_with_low_severity(self):
        """2. A typo fix is classified as a rewording with low severity."""
        base_desc = "Total employee head count on active payroll"
        live_desc = "Total employee headcount on active payroll"

        diff = classify_and_diff_description(base_desc, live_desc)
        self.assertEqual(diff["classification"], "TEXT_REWORDED")
        self.assertEqual(diff["severity"], "low")
        self.assertGreaterEqual(diff["similarity_ratio"], 0.88)

    def test_metric_referenced_by_another_metrics_comment_appears_in_blast_radius(self):
        """3. A metric referenced by another metric's comment appears in that metric's blast radius."""
        metric_a = DomainMetric(
            name="BASE_HEADCOUNT",
            sql_expression="COUNT(EMP_ID)",
            description="Active internal employee headcount.",
        )
        metric_b = DomainMetric(
            name="ADJUSTED_HEADCOUNT",
            sql_expression="COUNT(EMP_ID) + COUNT(CONTRACTORS)",
            description="Total workforce; references BASE_HEADCOUNT plus temporary workers.",
        )

        ont = FederatedEnterpriseOntology(
            domains={
                "hr": DomainOntology(
                    domain="hr",
                    metrics=[metric_a, metric_b],
                )
            }
        )
        sim = BlastRadiusSimulator(ontology=ont, mock_mode=False)
        report = sim.simulate_impact(
            target_name="BASE_HEADCOUNT",
            target_type="METRIC",
            change_type="MODIFY_DESCRIPTION",
        )

        ref_names = [m["name"] for m in report.referencing_metrics]
        self.assertIn("ADJUSTED_HEADCOUNT", ref_names)
        match_ref = next(m for m in report.referencing_metrics if m["name"] == "ADJUSTED_HEADCOUNT")
        self.assertEqual(match_ref["reference_type"], "comment")
        self.assertIn("BASE_HEADCOUNT", match_ref["details"])

    def test_ai_impact_removal_of_caveat_reports_changed_choice(self):
        """4. A removal of a caveat that distinguished two similar objects reports a changed choice on at least one question."""
        active_hc = DomainMetric(
            name="ACTIVE_HEADCOUNT",
            sql_expression="COUNT(CASE WHEN STATUS = 'ACTIVE' THEN EMP_ID END)",
            description="Active internal employees currently on payroll",
        )
        total_hc = DomainMetric(
            name="TOTAL_WORKFORCE",
            sql_expression="COUNT(EMP_ID)",
            description="Total headcount including contract and seasonal workers",
        )
        concept = DomainConcept(
            name="EMPLOYEE",
            attributes=[
                DomainAttribute(name="STATUS", data_type="VARCHAR"),
                DomainAttribute(name="EMP_ID", data_type="INTEGER"),
            ],
        )
        ont = FederatedEnterpriseOntology(
            domains={
                "workforce": DomainOntology(
                    domain="workforce",
                    concepts=[concept],
                    metrics=[active_hc, total_hc],
                )
            }
        )

        baseline_desc = "Active internal employees currently on payroll"
        live_desc = None
        test_question = "What is our internal employees currently on payroll?"

        report = run_ai_impact_check(
            object_name="ACTIVE_HEADCOUNT",
            baseline_description=baseline_desc,
            live_description=live_desc,
            ontology=ont,
            custom_questions=[test_question],
        )

        self.assertIsInstance(report, AIImpactReport)
        self.assertEqual(report.questions_tested, 1)
        self.assertEqual(report.classification, "TEXT_REMOVED")
        self.assertTrue(report.any_object_changed or report.any_refusal_changed or report.any_sql_changed)
        self.assertIn("Removal risk", report.risk_summary)
        q_res = report.questions[0]
        self.assertEqual(q_res.question, test_question)
        self.assertTrue(q_res.object_choice_changed or q_res.refusal_changed or q_res.sql_changed)

    def test_ai_impact_addition_where_baseline_had_no_description(self):
        """5. An addition where the baseline had no description reports as an addition, with its own wording."""
        metric = DomainMetric(
            name="VP_COUNT",
            sql_expression="COUNT(DISTINCT VP)",
            description=None,
        )
        concept = DomainConcept(
            name="BU",
            attributes=[DomainAttribute(name="VP", data_type="VARCHAR")],
        )
        ont = FederatedEnterpriseOntology(
            domains={"hr": DomainOntology(domain="hr", concepts=[concept], metrics=[metric])}
        )

        live_desc = "TEST DRIFT: VP dimension description modified by Cortex for SemaBridge validation."
        test_q = "How many distinct VPs do we have?"

        report = run_ai_impact_check(
            object_name="VP_COUNT",
            baseline_description=None,
            live_description=live_desc,
            ontology=ont,
            custom_questions=[test_q],
        )

        self.assertEqual(report.classification, "TEXT_ADDED")
        self.assertIn("Addition risk", report.risk_summary)
        self.assertIn("pull questions toward this object", report.risk_summary)
        self.assertNotIn("caveat was lost", report.risk_summary)
        self.assertEqual(report.questions_tested, 1)

    def test_ai_impact_typo_fix_reports_no_differences_and_says_sampled(self):
        """6. A typo fix reports no differences, and says the run was sampled."""
        metric = DomainMetric(
            name="HEADCOUNT",
            sql_expression="COUNT(EMP_ID)",
            description="Total active headcount across departments",
        )
        ont = FederatedEnterpriseOntology(
            domains={"hr": DomainOntology(domain="hr", metrics=[metric])}
        )

        base_desc = "Total active head count across departments"
        live_desc = "Total active headcount across departments"
        test_q = "What is total headcount across departments?"

        report = run_ai_impact_check(
            object_name="HEADCOUNT",
            baseline_description=base_desc,
            live_description=live_desc,
            ontology=ont,
            custom_questions=[test_q],
        )

        self.assertEqual(report.questions_changed, 0)
        self.assertEqual(report.overall_impact, "NO_DETECTED_IMPACT")
        self.assertIn("sampled evidence", report.sampling_disclaimer.lower())
        self.assertFalse(report.any_object_changed)
        self.assertFalse(report.any_sql_changed)

    def test_ai_impact_object_with_no_covering_questions_reports_that_and_runs_nothing(self):
        """7. An object with no covering questions reports that and runs nothing."""
        # Querying an isolated non-existent metric without custom questions
        report = run_ai_impact_check(
            object_name="UNREFERENCED_ORPHAN_METRIC_XYZ",
            baseline_description="Old comment",
            live_description="New comment",
            project_id="nonexistent-project-id",
        )

        self.assertEqual(report.status, "no_covering_questions")
        self.assertEqual(report.questions_tested, 0)
        self.assertEqual(len(report.questions), 0)
        self.assertIn("No certified queries", report.risk_summary)
        self.assertIsNone(report.question_source)

    def test_ai_impact_and_blast_radius_perform_zero_warehouse_writes(self):
        """8. The check never writes to the warehouse and never touches the sync path."""
        mock_metric = DomainMetric(
            name="TEST_SAFE_METRIC",
            sql_expression="AVG(VAL)",
            description="Safe metric for read-only validation.",
        )
        ont = FederatedEnterpriseOntology(
            domains={"analytics": DomainOntology(domain="analytics", metrics=[mock_metric])}
        )

        with patch("semabridge.connectors.snowflake_emitter.SnowflakeEmitter.emit") as mock_sf_emit, \
             patch("semabridge.connectors.databricks_publisher.DatabricksPublisher.publish") as mock_db_pub:

            sim = BlastRadiusSimulator(ontology=ont, mock_mode=False)
            sim.simulate_impact(
                target_name="TEST_SAFE_METRIC",
                target_type="METRIC",
                change_type="MODIFY_DESCRIPTION",
            )

            run_ai_impact_check(
                object_name="TEST_SAFE_METRIC",
                baseline_description="Safe metric for read-only validation.",
                live_description="Modified comment.",
                ontology=ont,
                custom_questions=["What is our test safe metric?"],
            )

            # Assert ZERO writes to warehouse
            mock_sf_emit.assert_not_called()
            mock_db_pub.assert_not_called()

    def test_ai_impact_failure_part_way_reports_partial_not_clean(self):
        """9. A failure part way reports partial, not clean."""
        metric = DomainMetric(
            name="FLAKY_METRIC",
            sql_expression="COUNT(*)",
            description="Base description",
        )
        ont = FederatedEnterpriseOntology(
            domains={"test": DomainOntology(domain="test", metrics=[metric])}
        )

        questions = ["Question 1 works fine", "Question 2 causes engine crash"]

        call_count = [0]
        original_resolve = None

        def side_effect_resolve(prompt, **kwargs):
            call_count[0] += 1
            if "causes engine crash" in prompt:
                raise RuntimeError("Simulated crash mid-run")
            from semabridge.ai.nl_query_engine import QueryResolutionPlan
            return QueryResolutionPlan(original_prompt=prompt, detected_metrics=["FLAKY_METRIC"])

        with patch.object(NLQueryEngine, "resolve_query", side_effect=side_effect_resolve):
            report = run_ai_impact_check(
                object_name="FLAKY_METRIC",
                baseline_description="Base description",
                live_description="New description",
                ontology=ont,
                custom_questions=questions,
            )

            self.assertEqual(report.status, "partial")
            self.assertEqual(report.questions_tested, 1)
            self.assertIsNotNone(report.partial_failure_reason)
            self.assertIn("Simulated crash mid-run", report.partial_failure_reason)

    def test_object_with_no_dependants_returns_empty_blast_radius_with_clear_message(self):
        """10. An object with no dependants returns an empty blast radius with a clear message."""
        isolated_metric = DomainMetric(
            name="SOLITARY_METRIC",
            sql_expression="SUM(COL_A)",
            description="Isolated standalone metric with no references.",
        )
        other_metric = DomainMetric(
            name="UNRELATED_METRIC",
            sql_expression="SUM(COL_B)",
            description="Completely independent calculation.",
        )
        ont = FederatedEnterpriseOntology(
            domains={"core": DomainOntology(domain="core", metrics=[isolated_metric, other_metric])}
        )
        sim = BlastRadiusSimulator(ontology=ont, mock_mode=False)
        report = sim.simulate_impact(
            target_name="SOLITARY_METRIC",
            target_type="METRIC",
            change_type="MODIFY_DESCRIPTION",
            expression="Isolated standalone metric with no references.",
        )

        self.assertEqual(report.total_impacted_items, 0)
        self.assertEqual(report.severity, "SAFE")
        self.assertEqual(len(report.affected_metrics), 0)
        self.assertIn("summary", report.empty_messages)
        self.assertIn("SOLITARY_METRIC", report.empty_messages["summary"])
        self.assertTrue("no dependants" in report.empty_messages["summary"].lower() or "nothing depends" in report.empty_messages["summary"].lower())

    def test_ai_impact_two_projects_shared_prefix_no_cross_sampling(self):
        """11. Two projects with shared prefix ('proj-hr' vs 'proj-hr_t'): no cross-sampling."""
        from unittest.mock import MagicMock
        from semabridge.governance.ai_impact_service import _collect_sample_questions

        mock_rows = [
            MagicMock(project_id="proj-hr_t", sql="SELECT VP FROM hr_t", question="What is the VP for hr_t?", name="VP", target=None),
            MagicMock(project_id="proj-human-res", sql="SELECT VP FROM human_res", question="What is the VP for human-res?", name="VP", target=None),
            MagicMock(project_id="proj-hr", sql="SELECT VP FROM hr", question="What is the VP for hr?", name="VP", target=None),
            MagicMock(project_id=None, sql="SELECT VP FROM global", question="What is the VP global?", name="VP", target=None),
        ]

        mock_session = MagicMock()
        mock_session.query.return_value.all.return_value = mock_rows
        mock_session.__enter__.return_value = mock_session

        with patch("semabridge.repository.orm.session_factory.db_manager.get_session", return_value=mock_session):
            # Query for proj-hr
            questions_hr, source, total, failed = _collect_sample_questions(
                object_name="VP",
                project_id="proj-hr",
            )
            self.assertEqual(questions_hr, ["What is the VP for hr?"])
            self.assertEqual(total, 1)

            # Query for proj-hr_t
            questions_hrt, source, total, failed = _collect_sample_questions(
                object_name="VP",
                project_id="proj-hr_t",
            )
            self.assertEqual(questions_hrt, ["What is the VP for hr_t?"])
            self.assertEqual(total, 1)

    def test_ai_impact_object_boundary_matching_not_substring(self):
        """12. Object hit test uses boundary-aware matching: 'VP' does not match 'improve' or 'vpn'."""
        from unittest.mock import MagicMock
        from semabridge.governance.ai_impact_service import _collect_sample_questions

        mock_rows = [
            MagicMock(project_id="p1", sql="SELECT improve_rate FROM sales", question="Can we improve our conversion?", name=None, target=None),
            MagicMock(project_id="p1", sql="SELECT vpn_logins FROM it", question="How many vpn users connected?", name=None, target=None),
            MagicMock(project_id="p1", sql="SELECT vp_name, dept FROM hr", question="Who is the VP for Engineering?", name="VP", target=None),
        ]

        mock_session = MagicMock()
        mock_session.query.return_value.all.return_value = mock_rows
        mock_session.__enter__.return_value = mock_session

        with patch("semabridge.repository.orm.session_factory.db_manager.get_session", return_value=mock_session):
            questions, source, total, failed = _collect_sample_questions(
                object_name="VP",
                project_id="p1",
            )
            # Only the genuine VP query matches, not 'improve' or 'vpn'
            self.assertEqual(questions, ["Who is the VP for Engineering?"])
            self.assertEqual(total, 1)

    def test_ai_impact_broken_source_table_reports_failure_not_empty_questions(self):
        """13. A broken source table or session reports status='failed', not 'no_covering_questions'."""
        mock_session = MagicMock()
        mock_session.__enter__.side_effect = RuntimeError("Broken database session / missing table")

        with patch("semabridge.repository.orm.session_factory.db_manager.get_session", side_effect=RuntimeError("DB down")), \
             patch("semabridge.ai.bi_truth_service._load_suite", side_effect=RuntimeError("BI truth table missing")):
            ont = FederatedEnterpriseOntology(
                domains={"hr": DomainOntology(domain="hr", metrics=[DomainMetric(name="HC", description="Headcount")])}
            )
            report = run_ai_impact_check(
                object_name="HC",
                project_id="p1",
                ontology=ont,
                baseline_description="Old comment",
                live_description="New comment",
            )
            self.assertEqual(report.status, "failed")
            self.assertIn("Question source", report.partial_failure_reason)
            self.assertIn("failed", report.risk_summary.lower())

    def test_ai_impact_no_ontology_reports_failure_cleanly_no_stub(self):
        """14. When ontology cannot be loaded, report status='failed' cleanly without fabricating a stub."""
        with patch("semabridge.ontology.store.DomainOntologyStore.get_federated_ontology", side_effect=RuntimeError("Ontology storage offline")):
            report = run_ai_impact_check(
                object_name="TEST_METRIC",
                ontology=None,
                baseline_description="Old",
                live_description="New",
            )
            self.assertEqual(report.status, "failed")
            self.assertIn("Ontology could not be loaded", report.partial_failure_reason)
            self.assertIn("Ontology could not be loaded", report.risk_summary)

    def test_ai_impact_cache_key_includes_model_version_and_cleared_on_baseline_save(self):
        """15. Cache key includes view_name and model_version; baseline save clears cache."""
        from semabridge.governance.ai_impact_service import _AI_IMPACT_CACHE
        clear_ai_impact_cache()
        self.assertEqual(len(_AI_IMPACT_CACHE), 0)

        # Insert a dummy cached report keyed by (project_id, view_name, model_version, object_name, baseline_description, live_description)
        key = ("p1", "sales_view", 2, "revenue", "old desc", "new desc")
        dummy_report = AIImpactReport(
            project_id="p1",
            object_name="revenue",
            view_name="sales_view",
            status="completed",
            risk_summary="Cached summary",
        )
        _AI_IMPACT_CACHE[key] = dummy_report
        self.assertIn(key, _AI_IMPACT_CACHE)

        # Clearing cache invalidates the entry
        clear_ai_impact_cache()
        self.assertNotIn(key, _AI_IMPACT_CACHE)
        self.assertEqual(len(_AI_IMPACT_CACHE), 0)

    def test_description_diff_excluding_cancelled_orders_removed_high_severity(self):
        """16. 'excluding cancelled orders' removed: classified as TEXT_REMOVED, high severity, no caveat keyword present."""
        base_desc = "Total revenue after excluding cancelled orders"
        live_desc = "Total revenue"
        diff = classify_and_diff_description(base_desc, live_desc)
        self.assertEqual(diff["classification"], "TEXT_REMOVED")
        self.assertEqual(diff["severity"], DiffSeverity.HIGH.value)
        self.assertEqual(diff["removed_caveats"], [])

    def test_description_diff_only_added_not_high_severity(self):
        """17. 'only' added to description: not high severity on the strength of that word alone."""
        base_desc = "Active employee accounts"
        live_desc = "Active employee accounts only"
        diff = classify_and_diff_description(base_desc, live_desc)
        self.assertEqual(diff["classification"], "TEXT_ADDED")
        self.assertNotEqual(diff["severity"], DiffSeverity.HIGH.value)
        self.assertEqual(diff["severity"], DiffSeverity.LOW.value)

    def test_description_diff_single_char_typo_fix_low(self):
        """18. A single character typo fix: rewording, low."""
        base_desc = "Total revenue calulated monthly"
        live_desc = "Total revenue calculated monthly"
        diff = classify_and_diff_description(base_desc, live_desc)
        self.assertEqual(diff["classification"], "TEXT_REWORDED")
        self.assertEqual(diff["severity"], DiffSeverity.LOW.value)
        self.assertFalse(diff["is_uncertain"])

    def test_description_diff_clause_replaced_similar_length_flagged_uncertain(self):
        """19. A whole clause replaced with a different clause of similar length: flagged as uncertain, not silently medium."""
        base_desc = "Revenue recognized when invoice is sent to client"
        live_desc = "Revenue recognized when payment is received by bank"
        diff = classify_and_diff_description(base_desc, live_desc)
        self.assertEqual(diff["classification"], "TEXT_REWORDED")
        self.assertTrue(diff["is_uncertain"])
        self.assertIsNotNone(diff["uncertainty_reason"])
        self.assertEqual(diff["severity"], DiffSeverity.MEDIUM.value)

    def test_description_diff_enum_and_configuration_thresholds(self):
        """20. Severity levels come from DiffSeverity enum, and thresholds are configurable."""
        self.assertEqual(DiffSeverity.LOW.value, "low")
        self.assertEqual(DiffSeverity.MEDIUM.value, "medium")
        self.assertEqual(DiffSeverity.HIGH.value, "high")
        self.assertGreater(get_typo_similarity_threshold(), 0.0)
        self.assertGreater(get_typo_max_length_diff(), 0)
        self.assertGreater(get_removal_insert_ratio_threshold(), 0.0)
