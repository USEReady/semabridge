"""
Governance: Anomaly Detection.

Detects unusual query patterns (cost, duration) using statistical methods.
"""

from typing import List, Dict, Any
import statistics
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class AnomalyDetector:
    """
    Detects anomalies in metrics (duration, credits, row count).
    """
    def detect_anomalies(self, measure_name: str, history: List[float]) -> Dict[str, Any]:
        """
        Uses rolling average +/- 2 sigma to detect anomalies.
        """
        if len(history) < 5:
            return {"is_anomaly": False}
            
        mean = statistics.mean(history[:-1])
        stdev = statistics.stdev(history[:-1])
        current = history[-1]
        
        # 2-sigma threshold
        upper_bound = mean + (2 * stdev)
        lower_bound = mean - (2 * stdev)
        
        is_anomaly = current > upper_bound or current < lower_bound
        
        if is_anomaly:
            logger.warning(f"ANOMALY DETECTED for {measure_name}: Current={current}, Expected={mean}")
            
        return {
            "is_anomaly": is_anomaly,
            "current_value": current,
            "expected_range": (lower_bound, upper_bound),
            "severity": "high" if current > (mean + 3 * stdev) else "medium"
        }
