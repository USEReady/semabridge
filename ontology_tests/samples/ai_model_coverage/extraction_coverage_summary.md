# Ontology extraction coverage: Artificial_Intelligence_Sample

Sources: SML `/tmp/ai/model.yaml` · ontology `/tmp/ai/ai.ontology.yaml` · OSI `/tmp/ai/osi.json` · PBIX `/mnt/user-data/uploads/Artificial_Intelligence_Sample.pbix`

Counts: sml_datasets 12, sml_columns 135, sml_metrics 22, sml_relationships 12, ontology_concepts 12, ontology_attributes 135, ontology_metrics 22, ontology_relationships 12, pbix_measures 22, pbix_relationships 10

## Where each gap belongs

| Status | Rows | Fix belongs in |
|---|---|---|
| OK | 443 | nothing to do |
| LOST_IN_ONTOLOGY | 22 | ontology generator (from_osi_model) |
| NO_ONTOLOGY_FIELD | 182 | ontology schema (models.py) |
| MISLABELED | 44 | ontology generator / schema |
| DERIVABLE | 1 | ontology generator (derive from model) |
| AUTO_ONLY | 178 | business input (only auto values today) |
| NOT_IN_SOURCE | 284 | business / governance input |

## Field-by-field

| Object | Field | Category | OK | LOST_BEFORE_SML | LOST_IN_ONTOLOGY | NO_ONTOLOGY_FIELD | MISLABELED | DERIVABLE | AUTO_ONLY | NOT_IN_SOURCE |
|---|---|---|---|---|---|---|---|---|---|---|
| column | calculated column expression | calculation |  |  |  | 9 |  |  |  |  |
| column | data_category | technical |  |  |  | 7 |  |  |  |  |
| column | data_type | technical | 135 |  |  |  |  |  |  |  |
| column | description | business |  |  |  |  |  |  |  | 135 |
| column | exists | technical | 135 |  |  |  |  |  |  |  |
| column | format_string | technical |  |  |  | 61 |  |  |  |  |
| column | is_hidden | technical |  |  |  | 29 |  |  |  |  |
| column | is_key | technical | 2 |  |  |  |  |  |  |  |
| column | sort_by_column | technical |  |  |  | 5 |  |  |  |  |
| column | synonyms | business | 1 |  |  |  |  |  | 134 |  |
| dataset | business_rules | business |  |  |  |  |  |  |  | 12 |
| dataset | data_steward | governance |  |  |  |  |  |  |  | 12 |
| dataset | description | business |  |  |  |  |  |  |  | 12 |
| dataset | exists | technical | 12 |  |  |  |  |  |  |  |
| dataset | fact/dimension role | analytical | 12 |  |  |  |  |  |  |  |
| dataset | is_hidden | technical |  |  |  | 3 |  |  |  |  |
| dataset | physical_table | technical | 12 |  |  |  |  |  |  |  |
| dataset | synonyms | business |  |  |  |  |  |  |  | 12 |
| domain | description | business |  |  |  |  |  |  |  | 1 |
| domain | owner | governance |  |  |  |  |  |  |  | 1 |
| hierarchy | levels | technical |  |  |  | 1 |  |  |  |  |
| metric | DAX expression (PBIX -> SML) | calculation | 22 |  |  |  |  |  |  |  |
| metric | DAX expression (original) | calculation |  |  |  |  | 22 |  |  |  |
| metric | aggregation | analytical |  |  |  |  | 22 |  |  |  |
| metric | business domain | governance |  |  |  |  |  |  | 22 |  |
| metric | business_rules | business |  |  |  |  |  |  |  | 22 |
| metric | complexity_tier | technical |  |  |  | 22 |  |  |  |  |
| metric | data_steward / owner | governance |  |  |  |  |  |  |  | 22 |
| metric | depends_on_measures | calculation |  |  |  | 9 |  |  |  |  |
| metric | description | business |  |  |  |  |  |  |  | 22 |
| metric | exists | technical | 22 |  |  |  |  |  |  |  |
| metric | format_string | technical |  |  |  | 21 |  |  |  |  |
| metric | grain | analytical |  |  |  |  |  | 1 |  | 21 |
| metric | home table (concept) | technical | 22 |  |  |  |  |  |  |  |
| metric | in PBIX | technical | 22 |  |  |  |  |  |  |  |
| metric | is_hidden | technical |  |  |  | 1 |  |  |  |  |
| metric | synonyms | business |  |  |  |  |  |  | 22 |  |
| metric | translated SQL expression | calculation |  |  | 22 |  |  |  |  |  |
| relationship | business meaning (predicate) | business |  |  |  |  |  |  |  | 12 |
| relationship | cardinality | technical | 12 |  |  |  |  |  |  |  |
| relationship | cross-filter direction | technical |  |  |  | 12 |  |  |  |  |
| relationship | exists | technical | 12 |  |  |  |  |  |  |  |
| relationship | in SML | technical | 10 |  |  |  |  |  |  |  |
| relationship | is_active | technical |  |  |  | 2 |  |  |  |  |
| relationship | join columns | technical | 12 |  |  |  |  |  |  |  |

## Notable findings (first examples)

- **metric `Case Count` · DAX expression (original)** (MISLABELED): DAX is stored in ontology 'sql_expression' (no DAX field); SQL consumers would get DAX — SML: `COUNTROWS('Cases')` — ontology: `COUNTROWS('Cases')`
- **metric `Case Count` · aggregation** (MISLABELED): calculated measure labelled SUM in the ontology — SML: `none` — ontology: `SUM`
- **metric `Cases MoM%` · grain** (DERIVABLE): grain available in SML (non-additive / group-by) but not copied — SML: `["'Calendar'[Date]"]` — ontology: `[]`
- **relationship `REL_OPPORTUNITIES_SYSTEMUSERSEQ__OWNERS_SYSTEMUSERSEQ` · is_active** (NO_ONTOLOGY_FIELD): inactive (USERELATIONSHIP) relationship looks active in the ontology — SML: `false`
- **hierarchy `Accounts.Location Hierarchy` · levels** (NO_ONTOLOGY_FIELD): ontology concepts have no hierarchy field — SML: `["Country", "State or Province", "City", "Street", "Account Name"]`

## Per-metric cards

**METRIC: Case Count**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Escalations**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: SLA Compliance**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Cases MoM%**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              DERIVABLE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Cases % by Product**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Cases % by Subject**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: CSAT Impact**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: CSAT Impact - Subject**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: CSAT Impact - Products**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: CSAT Impact - Agent**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Revenue Won**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Revenue In Pipeline**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Forecast %**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Forecast**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Opportunity Count In Pipeline**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Opportunity Count**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Count of Won**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Count of Lost**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Close %**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Revenue Open**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Forecast Adjustment Value**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    is_hidden                          NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```
**METRIC: Rev Goal**
```
  calculation:
    DAX expression (PBIX -> SML)       OK
    DAX expression (original)          MISLABELED
    translated SQL expression          LOST_IN_ONTOLOGY
    depends_on_measures                NO_ONTOLOGY_FIELD
  analytical:
    aggregation                        MISLABELED
    grain                              NOT_IN_SOURCE
  technical:
    in PBIX                            OK
    exists                             OK
    home table (concept)               OK
    format_string                      NO_ONTOLOGY_FIELD
    complexity_tier                    NO_ONTOLOGY_FIELD
  business:
    description                        NOT_IN_SOURCE
    synonyms                           AUTO_ONLY
    business_rules                     NOT_IN_SOURCE
  governance:
    data_steward / owner               NOT_IN_SOURCE
    business domain                    AUTO_ONLY
```