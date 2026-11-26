# Blast radius test pack

Covers risk per node, dependency tracing, the four drift categories, consumer
resolution, the model resolver, graph building, limits, and an end-to-end pass
against a running API.

Nothing here names a real project, metric, table or dashboard. Every fixture is
built inside the pack.

## Running

Against the reference implementation bundled here, which proves the suite is
self consistent:

    pytest blast_radius_tests -q
    # 53 passed

Against the product:

    SB_BR_IMPL=product pytest blast_radius_tests -q

Module overrides when paths differ:

    SB_BR_SIMULATOR_MODULE   default semabridge.governance.blast_radius
    SB_BR_RISK_MODULE        default semabridge.governance.risk_classifier
    SB_BR_RESOLVER_MODULE    default semabridge.governance.model_resolver

End to end, against a running server:

    SB_BR_API_BASE=http://localhost:8002 \
    SB_BR_PROJECT=<project id> \
    SB_BR_VIEW=<fully qualified view> \
    SB_BR_METRIC=<a metric other metrics depend on> \
    SB_BR_LONELY_METRIC=<an object nothing depends on> \
    SB_BR_OTHER_PROJECT=<a second project id> \
    SB_BR_TOKEN=<bearer token> \
    pytest blast_radius_tests/test_e2e_api.py -v

Without `SB_BR_API_BASE` the end-to-end file reports as not run, which is
accurate; it never passes without a server.

## What the product must expose

`risk_classifier`: `RiskLevel` with `BREAKING`, `ADVISORY`, `INFO`;
`classify_node_risk(change_type, item_type, reads_descriptions=False)`;
`report_risk(levels)`; the item constants `DEPENDANT`, `CERTIFIED`, `CONSUMER`,
`CONTEXT`.

`model_resolver`: `resolve_model(target_name, ontology_objects, adapter,
view_name, platform, target_type)` returning an object with `source` and
`reason`; the constants `ONTOLOGY`, `WAREHOUSE`, `NOT_FOUND`.

`blast_radius`: `BlastRadiusSimulator(metrics, certified_queries, truth_cases,
consumer_resolver, adapter, platform, max_depth, max_nodes)` with a
`simulate(target, target_type, category, change_type, expression, table,
view_name, project_id)` returning a report carrying `status`, `risk`, `nodes`,
`edges`, `messages`, `warehouse_references`, `suggestions`, `truncated`;
`Metric`; `build_graph(report)`.

A missing name fails with that name rather than skipping, because a missing name
is a gap in the contract.

## Files

| File | Covers |
| --- | --- |
| `test_risk.py` | Each change type and item type gets its level; the report's level is the highest node's; an empty report is INFO |
| `test_simulate.py` | Direct and transitive tracing, identifier boundaries, cycles, the four categories, empty results, consumer resolution and its failures, the model resolver, depth and node limits, graph building |
| `test_e2e_api.py` | The live route: every category answers, additions never report not found, node risks are one of three, the graph has no dangling edges, extra fields do not 422, another project is not read, two identical calls agree |
| `_ref/` | A working reference implementation, so the suite is proven rather than plausible |

## The cases that matter most

- `test_3_2_an_addition_never_reports_not_found` and `test_7_2`: the bug where
  opening blast radius on an addition searched the ontology and returned
  "target not found in scope".
- `test_4_6_nothing_is_invented_when_no_consumer_is_found`: the block that picked
  the first demo fixture when nothing matched.
- `test_7_11_another_project_is_not_read`: the global event lookup that matched
  on object name alone.
- `test_7_10_unknown_extra_fields_do_not_break_the_request`: the 422 the drift
  card hit when it sent the finding's category and expression.

## Stateful and platform cases

Consumer discovery per platform (Cortex Analyst models, data shares, Genie
Spaces, dashboards) is not covered here: those need the spike to confirm each
API reports its sources. The pack checks that a consumer resolver is called, that
its assets appear, and that a failure or an empty list is reported rather than
invented.
