"""Tests for Mandate 4: PK Resolution strict/permissive modes."""

import pytest
from semabridge.converter.osi_to_sml import OSIToSMLConverter
from semabridge.formats.sml.models import (
    SMLModel, SMLDataset, SMLColumn, SMLRelationship,
    DataType, Cardinality, CrossFilterDirection,
)


class TestPKIntegrityValidation:
    """Test _validate_pk_integrity in OSIToSMLConverter."""

    def _make_model(self, add_bad_rel=False):
        ds_customers = SMLDataset(
            unique_name="Customers",
            columns=[
                SMLColumn(unique_name="CustomerID", is_key=True, data_type=DataType.INTEGER),
                SMLColumn(unique_name="Name", data_type=DataType.STRING),
            ],
        )
        ds_orders = SMLDataset(
            unique_name="Orders",
            columns=[
                SMLColumn(unique_name="OrderID", is_key=True, data_type=DataType.INTEGER),
                SMLColumn(unique_name="CustomerID", data_type=DataType.INTEGER),
            ],
        )

        rels = [
            SMLRelationship(
                unique_name="Orders_Customers",
                from_dataset="Orders",
                from_columns=["CustomerID"],
                to_dataset="Customers",
                to_columns=["CustomerID"],
                cardinality=Cardinality.MANY_TO_ONE,
                cross_filter=CrossFilterDirection.SINGLE,
            ),
        ]

        if add_bad_rel:
            rels.append(
                SMLRelationship(
                    unique_name="Orders_Missing",
                    from_dataset="Orders",
                    from_columns=["BadCol"],
                    to_dataset="Customers",
                    to_columns=["NONEXISTENT_PK"],
                    cardinality=Cardinality.MANY_TO_ONE,
                    cross_filter=CrossFilterDirection.SINGLE,
                ),
            )

        return SMLModel(
            unique_name="TestModel",
            datasets=[ds_customers, ds_orders],
            relationships=rels,
        )

    def test_valid_pk_no_warnings(self, caplog):
        model = self._make_model(add_bad_rel=False)
        converter = OSIToSMLConverter(llm_config=False)
        converter._validate_pk_integrity(model)
        # No warnings expected for valid PKs
        pk_warnings = [r for r in caplog.records if "not found in dataset" in r.message]
        assert len(pk_warnings) == 0

    def test_invalid_pk_emits_warning(self, caplog):
        model = self._make_model(add_bad_rel=True)
        converter = OSIToSMLConverter(llm_config=False)
        converter._validate_pk_integrity(model)
        pk_warnings = [r for r in caplog.records if "NONEXISTENT_PK" in r.message]
        assert len(pk_warnings) >= 1
