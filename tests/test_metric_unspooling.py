"""Tests for Mandate 3: DAX Logical Unspooling — calculated column detection."""

import pytest
from semabridge.formats.sml.models import SMLColumn, DataType


class TestSMLColumnCalculated:
    """Test calculated column fields on SMLColumn."""

    def test_default_not_calculated(self):
        col = SMLColumn(unique_name="Revenue")
        assert col.is_calculated is False
        assert col.calculated_sql is None

    def test_mark_as_calculated(self):
        col = SMLColumn(
            unique_name="DaysSinceHire",
            is_calculated=True,
            calculated_sql="DATEDIFF('day', HIRE_DATE, CURRENT_DATE())",
        )
        assert col.is_calculated is True
        assert "DATEDIFF" in col.calculated_sql

    def test_calculated_with_data_type(self):
        col = SMLColumn(
            unique_name="NetRevenue",
            data_type=DataType.DECIMAL,
            is_calculated=True,
            calculated_sql="GROSS_REVENUE - DISCOUNTS",
        )
        assert col.is_calculated is True
        assert col.data_type == DataType.DECIMAL


class TestOSIToSMLCalculatedDetection:
    """Test that osi_to_sml converter detects computed columns."""

    def test_convert_column_with_source_expression(self):
        """Columns with source_expression should be marked calculated."""
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        from semabridge.intermediate.models import OSIColumn, OSIDataType

        osi_col = OSIColumn(
            unique_name="DaysSinceHire",
            data_type=OSIDataType.INTEGER,
            source_expression="DATEDIFF('day', HIRE_DATE, CURRENT_DATE())",
        )

        converter = OSIToSMLConverter(llm_config=False)
        sml_col = converter._convert_column(osi_col)

        assert sml_col.is_calculated is True
        assert sml_col.calculated_sql == "DATEDIFF('day', HIRE_DATE, CURRENT_DATE())"

    def test_convert_column_without_source_expression(self):
        """Regular columns should NOT be marked calculated."""
        from semabridge.converter.osi_to_sml import OSIToSMLConverter
        from semabridge.intermediate.models import OSIColumn, OSIDataType

        osi_col = OSIColumn(
            unique_name="Revenue",
            data_type=OSIDataType.DECIMAL,
        )

        converter = OSIToSMLConverter(llm_config=False)
        sml_col = converter._convert_column(osi_col)

        assert sml_col.is_calculated is False
        assert sml_col.calculated_sql is None
