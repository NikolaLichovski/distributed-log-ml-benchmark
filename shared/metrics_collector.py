"""
Metrics collection library for distributed performance monitoring.
Provides thread-safe metric buffering and batch insertion to minimize database overhead.
"""

import psycopg2
import threading
import time
import logging
from datetime import datetime
from typing import Dict, List, Optional
from collections import deque

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Thread-safe metrics collector with batched database writes.
    Designed to minimize performance impact on monitored components.
    """

    def __init__(self,
                 experiment_id: int,
                 component_type: str,
                 component_id: str,
                 db_config: Dict,
                 batch_size: int = 50,
                 flush_interval: float = 10.0):
        """
        Initialize metrics collector.

        Args:
            experiment_id: Unique identifier for the experimental run
            component_type: Type of component ('producer' or 'consumer')
            component_id: Unique identifier for this component instance
            db_config: PostgreSQL connection parameters
            batch_size: Number of metrics to buffer before flushing
            flush_interval: Maximum seconds between flushes
        """
        self.experiment_id = experiment_id
        self.component_type = component_type
        self.component_id = component_id
        self.db_config = db_config
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        self.metrics_buffer = deque()
        self.lag_buffer = deque()
        self.lock = threading.Lock()
        self.last_flush = time.time()
        self.running = True

        # Start background flush thread
        self.flush_thread = threading.Thread(target=self._periodic_flush, daemon=True)
        self.flush_thread.start()

        logger.info(f"MetricsCollector initialized for {component_type}:{component_id} in experiment {experiment_id}")

    def record_metric(self, metric_name: str, metric_value: float, timestamp: Optional[datetime] = None):
        """
        Record a performance metric.

        Args:
            metric_name: Name of the metric (e.g., 'throughput', 'latency')
            metric_value: Numeric value of the metric
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        metric = {
            'experiment_id': self.experiment_id,
            'component_type': self.component_type,
            'component_id': self.component_id,
            'metric_name': metric_name,
            'metric_value': float(metric_value),
            'timestamp': timestamp
        }

        with self.lock:
            self.metrics_buffer.append(metric)

            # Flush if buffer is full
            if len(self.metrics_buffer) >= self.batch_size:
                self._flush_metrics()

    def record_lag(self, consumer_id: str, partition: int,
                   current_offset: int, log_end_offset: int, timestamp: Optional[datetime] = None):
        """
        Record consumer lag information.

        Args:
            consumer_id: Identifier for the consumer
            partition: Kafka partition number
            current_offset: Consumer's current offset position
            log_end_offset: Latest offset in the partition
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        lag = log_end_offset - current_offset

        lag_record = {
            'experiment_id': self.experiment_id,
            'consumer_id': consumer_id,
            'partition': partition,
            'current_offset': current_offset,
            'log_end_offset': log_end_offset,
            'lag': lag,
            'timestamp': timestamp
        }

        with self.lock:
            self.lag_buffer.append(lag_record)

            # Flush if buffer is full
            if len(self.lag_buffer) >= self.batch_size:
                self._flush_lag()

    def _periodic_flush(self):
        """Background thread that periodically flushes buffered metrics."""
        while self.running:
            time.sleep(self.flush_interval)

            with self.lock:
                if time.time() - self.last_flush >= self.flush_interval:
                    if self.metrics_buffer:
                        self._flush_metrics()
                    if self.lag_buffer:
                        self._flush_lag()

    def _flush_metrics(self):
        """Flush buffered metrics to database."""
        if not self.metrics_buffer:
            return

        metrics_to_insert = list(self.metrics_buffer)
        self.metrics_buffer.clear()

        try:
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor()

            # Batch insert using execute_values for efficiency
            from psycopg2.extras import execute_values

            values = [
                (
                    m['experiment_id'],
                    m['component_type'],
                    m['component_id'],
                    m['metric_name'],
                    m['metric_value'],
                    m['timestamp']
                )
                for m in metrics_to_insert
            ]

            execute_values(
                cursor,
                """
                INSERT INTO performance_metrics 
                (experiment_id, component_type, component_id, metric_name, metric_value, timestamp)
                VALUES %s
                """,
                values
            )

            conn.commit()
            cursor.close()
            conn.close()

            self.last_flush = time.time()
            logger.debug(f"Flushed {len(values)} metrics to database")

        except Exception as e:
            logger.error(f"Error flushing metrics: {e}")
            # Re-add metrics to buffer for retry
            with self.lock:
                self.metrics_buffer.extend(metrics_to_insert)

    def _flush_lag(self):
        """Flush buffered lag records to database."""
        if not self.lag_buffer:
            return

        lag_to_insert = list(self.lag_buffer)
        self.lag_buffer.clear()

        try:
            conn = psycopg2.connect(**self.db_config)
            cursor = conn.cursor()

            from psycopg2.extras import execute_values

            values = [
                (
                    l['experiment_id'],
                    l['consumer_id'],
                    l['partition'],
                    l['current_offset'],
                    l['log_end_offset'],
                    l['lag'],
                    l['timestamp']
                )
                for l in lag_to_insert
            ]

            execute_values(
                cursor,
                """
                INSERT INTO consumer_lag 
                (experiment_id, consumer_id, partition, current_offset, log_end_offset, lag, timestamp)
                VALUES %s
                """,
                values
            )

            conn.commit()
            cursor.close()
            conn.close()

            logger.debug(f"Flushed {len(values)} lag records to database")

        except Exception as e:
            logger.error(f"Error flushing lag records: {e}")
            # Re-add lag records to buffer for retry
            with self.lock:
                self.lag_buffer.extend(lag_to_insert)

    def flush(self):
        """Force immediate flush of all buffered data."""
        with self.lock:
            self._flush_metrics()
            self._flush_lag()

    def shutdown(self):
        """Shutdown the collector and flush remaining data."""
        logger.info("Shutting down MetricsCollector...")
        self.running = False

        if self.flush_thread.is_alive():
            self.flush_thread.join(timeout=5)

        self.flush()
        logger.info("MetricsCollector shutdown complete")


class PerformanceMonitor:
    """
    High-level performance monitoring with automatic metric calculation.
    Tracks throughput, latency, and other derived metrics.
    """

    def __init__(self, metrics_collector: MetricsCollector):
        self.collector = metrics_collector
        self.message_count = 0
        self.start_time = time.time()
        self.latencies = deque(maxlen=1000)  # Keep last 1000 for statistics
        self.window_start = time.time()
        self.window_messages = 0

    def record_message(self, latency_ms: Optional[float] = None):
        """
        Record a processed message and optionally its latency.

        Args:
            latency_ms: Message processing latency in milliseconds
        """
        self.message_count += 1
        self.window_messages += 1

        if latency_ms is not None:
            self.latencies.append(latency_ms)

        # Calculate windowed throughput every 100 messages
        if self.window_messages >= 100:
            self._calculate_window_metrics()

    def _calculate_window_metrics(self):
        """Calculate and record metrics for the current time window."""
        current_time = time.time()
        window_duration = current_time - self.window_start

        if window_duration > 0:
            throughput = self.window_messages / window_duration
            self.collector.record_metric('throughput', throughput)

            if self.latencies:
                import statistics
                avg_latency = statistics.mean(self.latencies)
                p95_latency = statistics.quantiles(self.latencies, n=20)[18]  # 95th percentile

                self.collector.record_metric('latency_avg', avg_latency)
                self.collector.record_metric('latency_p95', p95_latency)

        # Reset window
        self.window_start = current_time
        self.window_messages = 0

    def get_total_throughput(self) -> float:
        """Calculate total average throughput since start."""
        elapsed = time.time() - self.start_time
        return self.message_count / elapsed if elapsed > 0 else 0

    def shutdown(self):
        """Flush final metrics before shutdown."""
        if self.window_messages > 0:
            self._calculate_window_metrics()
        self.collector.flush()