-- read back from the warehouse, reformatted on its way out
create or replace semantic view db1.sch1.view_one
	tables (
		DB1.SCH1.DIM_ONE primary key (DIM_ONE_ID),
		ENTITY as DB1.SCH1.ENTITY_ENRICHED
	)
	relationships (
		ENTITY_DIMONE as ENTITY(DIM_ONE_ID) references DIM_ONE(DIM_ONE_ID)
	)
	facts (
		PRIVATE ENTITY.AMOUNT as entity."AMOUNT"
	)
	dimensions (
		DIM_ONE.LABEL as "DIM_ONE"."LABEL" with synonyms=('Label','LABEL'),
		PRIVATE ENTITY.FLAG as ENTITY."FLAG",
		ENTITY.CATEGORY as ENTITY."CATEGORY" comment='Category of the row'
	)
	metrics (
		ENTITY.TOTAL_AMOUNT as sum( ENTITY.AMOUNT :: FLOAT )   with synonyms=('Total','TOTAL_AMOUNT') comment='Total   amount',
		ENTITY.ITEM_COUNT non additive by (DIM_ONE.LABEL asc nulls last) as COUNT(ENTITY.ITEM_ID) with synonyms=('Count'),
		ENTITY.FLAGGED_RATE as ((COUNT(CASE WHEN (ENTITY.FLAG IS NOT NULL) THEN ENTITY.ITEM_ID END)) / NULLIF(COUNT(ENTITY.ITEM_ID), 0)),
		ENTITY.UNRESOLVED as NULL::DOUBLE comment='not applicable: depends on an unresolved metric',
		ENTITY.TYPED_TOTAL as CAST(SUM(ENTITY.AMOUNT) AS INT) comment='Cast on purpose'
	);
