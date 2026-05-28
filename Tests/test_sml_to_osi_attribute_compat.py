from semabridge.converter.sml_to_osi import SMLToOSIConverter
from semabridge.intermediate.models import OSIAttribute


def test_convert_dimension_to_osi_accepts_official_osi_attribute_shape():
    converter = SMLToOSIConverter()
    dim = type(
        "Dim",
        (),
        {
            "unique_name": "date_dim",
            "label": "Date",
            "description": "",
            "dataset": "date",
            "attributes": [
                OSIAttribute(
                    unique_name="month_no",
                    dataset="date",
                    source_column="MonthNo",
                )
            ],
            "hierarchies": [],
            "is_hidden": False,
        },
    )()

    osi_dim = converter._convert_dimension_to_osi(dim)
    assert osi_dim.attributes[0].source_column == "MonthNo"
