"""
Edge Case Test Generator for DAX Translation.

Automatically generates complex test cases to stress-test 
the semantic parity engine.
"""

from typing import List, Dict
import random

class EdgeCaseTestGenerator:
    """
    Generates complex DAX test cases for parity testing.
    """
    
    def generate_test_cases(self, count: int = 10) -> List[Dict[str, str]]:
        test_cases = []
        
        patterns = [
            self._gen_nested_calculate,
            self._gen_null_handling,
            self._gen_extreme_arithmetic,
            self._gen_iterator_with_filter
        ]
        
        for i in range(count):
            pattern = random.choice(patterns)
            test_cases.append(pattern(i))
            
        return test_cases

    def _gen_nested_calculate(self, seed: int) -> Dict[str, str]:
        depth = (seed % 3) + 1
        dax = "SUM('Sales'[Amount])"
        for d in range(depth):
            dax = f"CALCULATE({dax}, 'Date'[Year] = {2020 + d})"
            
        return {
            "name": f"nested_calculate_{depth}",
            "dax": dax,
            "category": "CALCULATE"
        }

    def _gen_null_handling(self, seed: int) -> Dict[str, str]:
        dax = f"IF(ISBLANK(SUM('Sales'[Amount])), 0, SUM('Sales'[Amount])) + {seed}"
        return {
            "name": f"null_handling_{seed}",
            "dax": dax,
            "category": "NULLS"
        }

    def _gen_extreme_arithmetic(self, seed: int) -> Dict[str, str]:
        dax = f"(SUM('Sales'[Amount]) * 1.0000001) / (COUNTROWS('Sales') + {seed})"
        return {
            "name": f"extreme_arithmetic_{seed}",
            "dax": dax,
            "category": "ARITHMETIC"
        }

    def _gen_iterator_with_filter(self, seed: int) -> Dict[str, str]:
        dax = f"SUMX(FILTER('Sales', 'Sales'[Amount] > {seed * 100}), 'Sales'[Amount] * 1.1)"
        return {
            "name": f"iterator_filter_{seed}",
            "dax": dax,
            "category": "ITERATORS"
        }
