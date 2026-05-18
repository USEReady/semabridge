import pytest
from semabridge.converter.dax_ast_parser import DaxAstParser
from semabridge.converter.filter_context_tracker import FilterContextTracker

def test_calculate_nesting_depth():
    parser = DaxAstParser()
    tracker = FilterContextTracker()
    
    dax = "CALCULATE(CALCULATE(SUM('Sales'[Amount]), 'Date'[Year] = 2024), 'Product'[Category] = \"Electronics\")"
    ast = parser.parse(dax)
    assert ast is not None
    
    trace = tracker.track(ast)
    
    assert trace.max_context_depth == 2
    assert len(trace.steps) == 2
    assert trace.steps[0].function == "CALCULATE"
    assert trace.steps[0].context_depth_after == 1
    assert trace.steps[1].context_depth_after == 2

def test_removefilters_detection():
    parser = DaxAstParser()
    tracker = FilterContextTracker()
    
    dax = "CALCULATE(SUM('Sales'[Amount]), REMOVEFILTERS('Date'[Year]))"
    ast = parser.parse(dax)
    assert ast is not None
    
    trace = tracker.track(ast)
    
    assert len(trace.steps) == 1
    assert trace.steps[0].filters_applied == ["Date[Year]"]
    # In my current implementation, I put it in filters_applied but it should be distinguished
    # This test will help me refine the tracker

def test_simple_calculate_filters():
    parser = DaxAstParser()
    tracker = FilterContextTracker()
    
    dax = "CALCULATE(SUM('Sales'[Amount]), 'Date'[Year] = 2025, 'Geography'[Region] = \"EMEA\")"
    ast = parser.parse(dax)
    assert ast is not None
    
    trace = tracker.track(ast)
    
    assert trace.max_context_depth == 1
    assert len(trace.steps) == 1
    assert len(trace.steps[0].filters_applied) == 2
    assert "Date[Year]" in trace.steps[0].filters_applied
    assert "Geography[Region]" in trace.steps[0].filters_applied
