#!/usr/bin/env python
"""
Quick test script to verify Gemini LLM integration and run measure pipeline.
"""
import sys
import json
sys.path.insert(0, 'src')

from semabridge.converter.gemini_dax_translator import get_gemini_translator

def test_llm_translator():
    """Test if Gemini LLM translator is properly initialized."""
    print("Testing Gemini LLM Translator initialization...")
    translator = get_gemini_translator()
    
    if not translator.api_key:
        print("[FAIL] Gemini client not initialized - GEMINI_API_KEY not set or invalid")
        return False
    
    print(f"[OK] Gemini LLM client initialized")
    print(f"   Model preferences: {translator.model_preferences}")
    print(f"   Cache file: {translator.cache_file}")
    print(f"   Cached entries: {len(translator.cache)}")
    
    # Test a simple DAX translation
    print("\nTesting DAX translation...")
    test_dax = "SUM([Revenue])"
    result = translator.translate(
        dax=test_dax,
        table_alias="sales",
        dataset_name="TestDataset",
        metric_name="Total Revenue"
    )
    
    print(f"  DAX: {test_dax}")
    print(f"  SQL: {result.sql}")
    print(f"  Valid: {result.is_valid}")
    print(f"  Confidence: {result.confidence:.2f}")
    print(f"  Model used: {result.model}")
    print(f"  Cached: {result.cached}")
    
    return result.is_valid

def analyze_failures():
    """Analyze current measure failures."""
    print("\n" + "="*80)
    print("ANALYZING MEASURE FAILURES")
    print("="*80)
    
    try:
        # Find latest analysis file
        import glob
        files = glob.glob("output/measure_analysis_Core_Finance_v1_*.json")
        if not files:
            print("No analysis files found")
            return
        
        latest_file = max(files, key=lambda x: x)
        with open(latest_file) as f:
            data = json.load(f)
        
        stats = data.get("summary", {})
        print(f"\nDataset: Core_Finance_v1")
        print(f"Total Measures: {stats.get('total_measures')}")
        print(f"Success Rate: {stats.get('success_rate')}")
        print(f"Complete: {stats.get('complete_conversion')}")
        print(f"Partial: {stats.get('partial_conversion')}")
        
        # Show a sample of measures
        measures = data.get("measures", {})
        print(f"\nSample measures:")
        for i, (name, measure) in enumerate(list(measures.items())[:5]):
            print(f"  - {name}: {measure.get('status', 'unknown')}")
            if i > 4:
                break
                
    except Exception as e:
        print(f"Error reading results: {e}")

if __name__ == "__main__":
    success = test_llm_translator()
    analyze_failures()
    
    if success:
        print("\n[OK] Gemini LLM integration is working!")
        print("\nNext steps:")
        print("1. Run: python tests/test_measure_pipeline_detailed.py --dataset <dataset>")
        print("2. The pipeline will now use Gemini for complex DAX expressions")
        print("3. Results will be cached to avoid repeated API calls")
    else:
        print("\n[FAIL] Gemini LLM integration failed")
        print("Please check GEMINI_API_KEY in .env file")
