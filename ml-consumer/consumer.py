import json
import psycopg2
import pandas as pd
import time
import logging
import os
import sys
import socket
from datetime import datetime
from confluent_kafka import Consumer, KafkaError, TopicPartition
from model import AnomalyDetector

# Add shared module to path
sys.path.insert(0, '/app/shared')
from metrics_collector import MetricsCollector, PerformanceMonitor

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration from environment
KAFKA_BOOTSTRAP_SERVERS = os.getenv('KAFKA_BOOTSTRAP_SERVERS', 'kafka:9094')
KAFKA_TOPIC = os.getenv('KAFKA_TOPIC', 'logs')
KAFKA_GROUP_ID = os.getenv('KAFKA_GROUP_ID', 'ml-consumer-group')
EXPERIMENT_ID = int(os.getenv('EXPERIMENT_ID', '0'))
CONSUMER_ID = os.getenv('CONSUMER_ID', socket.gethostname())

POSTGRES_CONN = {
    'host': os.getenv('POSTGRES_HOST', 'db'),
    'database': os.getenv('POSTGRES_DB', 'logsdb'),
    'user': os.getenv('POSTGRES_USER', 'postgres'),
    'password': os.getenv('POSTGRES_PASSWORD', 'postgres')
}


class LogConsumer:
    """
    ML-powered log consumer with integrated performance monitoring.
    Processes log messages, detects anomalies, and tracks performance metrics.
    """

    def __init__(self):
        self.detector = AnomalyDetector()
        self.conn = None
        self.cursor = None
        self.consumer = None
        self.processed_count = 0
        self.experiment_id = EXPERIMENT_ID
        self.consumer_id = CONSUMER_ID

        # Initialize metrics collection if experiment is active
        self.metrics_enabled = EXPERIMENT_ID > 0
        if self.metrics_enabled:
            self.metrics_collector = MetricsCollector(
                experiment_id=EXPERIMENT_ID,
                component_type='consumer',
                component_id=CONSUMER_ID,
                db_config=POSTGRES_CONN,
                batch_size=50,
                flush_interval=10.0
            )
            self.performance_monitor = PerformanceMonitor(self.metrics_collector)
            logger.info(f"Metrics collection enabled for experiment {EXPERIMENT_ID}")
        else:
            logger.info("Metrics collection disabled (no experiment ID)")

        # Load existing model
        self.detector.load_model()

        # Lag tracking
        self.assigned_partitions = []
        self.last_lag_check = time.time()
        self.lag_check_interval = 5.0  # Check lag every 5 seconds

    def connect_db(self, max_retries=30):
        """Connect to PostgreSQL with retry logic."""
        for attempt in range(max_retries):
            try:
                self.conn = psycopg2.connect(**POSTGRES_CONN)
                self.conn.autocommit = True
                self.cursor = self.conn.cursor()
                logger.info("Connected to PostgreSQL")
                return True
            except Exception as e:
                logger.warning(f"Database connection attempt {attempt + 1}/{max_retries} failed: {e}")
                time.sleep(2)
        return False

    def setup_consumer(self):
        """Setup Kafka consumer with partition assignment callback."""
        consumer_conf = {
            'bootstrap.servers': KAFKA_BOOTSTRAP_SERVERS,
            'group.id': KAFKA_GROUP_ID,
            'auto.offset.reset': 'earliest',
            'enable.auto.commit': False,
            'max.poll.interval.ms': 300000,
            'session.timeout.ms': 10000,
            'heartbeat.interval.ms': 3000
        }

        self.consumer = Consumer(consumer_conf)
        self.consumer.subscribe(
            [KAFKA_TOPIC],
            on_assign=self.on_assign
        )
        logger.info(f"Kafka consumer setup complete (Group: {KAFKA_GROUP_ID})")

    def on_assign(self, consumer, partitions):
        """Callback when partitions are assigned to this consumer."""
        self.assigned_partitions = partitions
        partition_list = [p.partition for p in partitions]
        logger.info(f"Assigned partitions: {partition_list}")

        # Record initial lag
        self.check_and_record_lag()

    def wait_for_topic(self, max_retries=60):
        """Wait for Kafka topic to be available."""
        for attempt in range(max_retries):
            try:
                metadata = self.consumer.list_topics(timeout=5)
                if KAFKA_TOPIC in metadata.topics:
                    logger.info(f"Topic '{KAFKA_TOPIC}' is available")
                    return True
                else:
                    logger.warning(f"Topic '{KAFKA_TOPIC}' not found, waiting... attempt {attempt + 1}")
            except Exception as e:
                logger.warning(f"Error checking topic: {e}, attempt {attempt + 1}")
            time.sleep(2)
        return False

    def check_and_record_lag(self):
        """Measure and record consumer lag for assigned partitions."""
        if not self.metrics_enabled or not self.assigned_partitions:
            return

        try:
            for partition in self.assigned_partitions:
                # Get current position
                try:
                    current_offset = self.consumer.position([partition])[0].offset
                except Exception:
                    continue  # Partition may not have position yet

                # Get watermark offsets (high watermark = latest offset)
                low, high = self.consumer.get_watermark_offsets(partition, timeout=1.0)

                # Record lag
                self.metrics_collector.record_lag(
                    consumer_id=self.consumer_id,
                    partition=partition.partition,
                    current_offset=current_offset,
                    log_end_offset=high,
                    timestamp=datetime.utcnow()
                )

        except Exception as e:
            logger.debug(f"Error checking lag: {e}")

    def insert_log(self, log):
        """Insert log into database and return log ID."""
        try:
            self.cursor.execute(
                """
                INSERT INTO logs (timestamp, service, log_level, message)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (log['timestamp'], log['service'], log['log_level'], log['message'])
            )
            return self.cursor.fetchone()[0]
        except Exception as e:
            logger.error(f"Error inserting log: {e}")
            self.reconnect_db()
            return None

    def insert_anomaly(self, log_id, is_anomaly, score):
        """Insert anomaly result into database."""
        try:
            is_anomaly_python = bool(is_anomaly)
            score_python = float(score)

            self.cursor.execute(
                """
                INSERT INTO anomalies (log_id, is_anomaly, anomaly_score)
                VALUES (%s, %s, %s)
                """,
                (log_id, is_anomaly_python, score_python)
            )
        except Exception as e:
            logger.error(f"Error inserting anomaly: {e}")
            self.reconnect_db()

    def reconnect_db(self):
        """Reconnect to database."""
        try:
            if self.conn:
                self.conn.close()
            self.connect_db(max_retries=5)
        except Exception as e:
            logger.error(f"Error reconnecting to database: {e}")

    def fetch_training_data(self):
        """Fetch historical logs for model training."""
        try:
            query = """
                SELECT timestamp, service, log_level, message 
                FROM logs 
                ORDER BY timestamp DESC 
                LIMIT 1000
            """
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return pd.read_sql_query(query, self.conn)
        except Exception as e:
            logger.error(f"Error fetching training data: {e}")
            return pd.DataFrame()

    def retrain_model_if_needed(self):
        """Retrain model if not trained or periodically."""
        if not self.detector.is_trained and self.processed_count % 20 == 0:
            logs_df = self.fetch_training_data()
            if not logs_df.empty and len(logs_df) >= 10:
                logger.info(f"Training model with {len(logs_df)} samples...")
                success = self.detector.train(logs_df)
                if success:
                    logger.info("Model training completed successfully")
                else:
                    logger.warning("Model training failed")

    def process_message(self, msg):
        """Process a single Kafka message with timing for latency measurement."""
        process_start = time.time()

        try:
            log = json.loads(msg.value().decode('utf-8'))

            # Insert log into database
            log_id = self.insert_log(log)
            if log_id is None:
                logger.warning("Failed to insert log, skipping anomaly detection")
                return

            # Retrain model if needed
            self.retrain_model_if_needed()

            # Predict anomaly
            is_anomaly, score = self.detector.predict(log)

            # Insert anomaly result
            self.insert_anomaly(log_id, is_anomaly, score)

            self.processed_count += 1

            # Record processing metrics
            process_time_ms = (time.time() - process_start) * 1000
            if self.metrics_enabled:
                self.performance_monitor.record_message(process_time_ms)

            if is_anomaly:
                logger.warning(
                    f"🚨 ANOMALY DETECTED: {log['service']} - "
                    f"{log['message'][:100]}... (score: {score:.3f})"
                )

            if self.processed_count % 100 == 0:
                if self.metrics_enabled:
                    throughput = self.performance_monitor.get_total_throughput()
                    logger.info(
                        f"Processed {self.processed_count} messages "
                        f"(avg throughput: {throughput:.2f} msg/s)"
                    )
                else:
                    logger.info(f"Processed {self.processed_count} messages")

        except json.JSONDecodeError as e:
            logger.error(f"Error decoding JSON message: {e}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")

    def run(self):
        """Main consumer loop with lag tracking."""
        if not self.connect_db():
            logger.error("Failed to connect to database")
            return

        self.setup_consumer()

        # Wait for topic to be available
        if not self.wait_for_topic():
            logger.error("Topic not available, exiting")
            return

        logger.info(f"ML Consumer started: {self.consumer_id}")
        logger.info(f"Experiment ID: {self.experiment_id}")

        try:
            consecutive_errors = 0
            max_consecutive_errors = 10

            while True:
                msg = self.consumer.poll(timeout=1.0)

                # Periodically check and record lag
                if self.metrics_enabled and (time.time() - self.last_lag_check) >= self.lag_check_interval:
                    self.check_and_record_lag()
                    self.last_lag_check = time.time()

                if msg is None:
                    continue

                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        continue
                    else:
                        logger.error(f"Kafka error: {msg.error()}")
                        consecutive_errors += 1
                        if consecutive_errors >= max_consecutive_errors:
                            logger.error("Too many consecutive errors, exiting")
                            break
                        continue

                # Reset error counter on successful message
                consecutive_errors = 0

                self.process_message(msg)

                try:
                    self.consumer.commit(msg)
                except Exception as e:
                    logger.error(f"Error committing message: {e}")

        except KeyboardInterrupt:
            logger.info("Received shutdown signal")
        except Exception as e:
            logger.error(f"Consumer error: {e}", exc_info=True)
        finally:
            self.cleanup()

    def cleanup(self):
        """Clean up resources and flush metrics."""
        logger.info("Cleaning up resources...")

        try:
            if self.metrics_enabled:
                self.performance_monitor.shutdown()
                self.metrics_collector.shutdown()
        except Exception as e:
            logger.error(f"Error shutting down metrics: {e}")

        try:
            if self.consumer:
                self.consumer.close()
        except Exception as e:
            logger.error(f"Error closing consumer: {e}")

        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except Exception as e:
            logger.error(f"Error closing database connection: {e}")

        logger.info("Cleanup complete")


if __name__ == "__main__":
    consumer = LogConsumer()
    consumer.run()