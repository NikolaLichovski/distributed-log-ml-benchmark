import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import LabelEncoder
import pickle
import os
import logging

logger = logging.getLogger(__name__)

MODEL_PATH = "/app/models/isolation_forest.pkl"
ENCODERS_PATH = "/app/models/encoders.pkl"

# Ensure model directory exists
os.makedirs("/app/models", exist_ok=True)


class AnomalyDetector:
    def __init__(self):
        self.model = None
        self.service_encoder = LabelEncoder()
        self.level_encoder = LabelEncoder()
        self.is_trained = False

    def extract_features(self, logs_df):
        """Extract features from logs for ML model"""
        features_df = logs_df.copy()

        # Encode categorical variables
        if not self.is_trained:
            # Fit encoders on training data
            features_df['service_encoded'] = self.service_encoder.fit_transform(features_df['service'])
            features_df['level_encoded'] = self.level_encoder.fit_transform(features_df['log_level'])
        else:
            # Handle unseen categories
            features_df['service_encoded'] = self._safe_transform(
                self.service_encoder, features_df['service']
            )
            features_df['level_encoded'] = self._safe_transform(
                self.level_encoder, features_df['log_level']
            )

        # Extract message features
        features_df['message_length'] = features_df['message'].str.len()
        features_df['error_keywords'] = features_df['message'].str.lower().str.contains(
            'error|fail|exception|timeout|unavailable'
        ).astype(int)
        features_df['warning_keywords'] = features_df['message'].str.lower().str.contains(
            'warn|high|memory|retry|limit'
        ).astype(int)

        # Time-based features
        if 'timestamp' in features_df.columns:
            features_df['timestamp'] = pd.to_datetime(features_df['timestamp'])
            features_df['hour'] = features_df['timestamp'].dt.hour
            features_df['minute'] = features_df['timestamp'].dt.minute
        else:
            features_df['hour'] = 12  # Default values
            features_df['minute'] = 0

        feature_columns = [
            'service_encoded', 'level_encoded', 'message_length',
            'error_keywords', 'warning_keywords', 'hour', 'minute'
        ]

        return features_df[feature_columns]

    def _safe_transform(self, encoder, data):
        """Safely transform data, handling unseen categories"""
        result = []
        for item in data:
            try:
                encoded = encoder.transform([item])[0]
                result.append(encoded)
            except ValueError:
                # Use a default value for unseen categories
                result.append(0)
        return result

    def train(self, logs_df):
        """Train the anomaly detection model"""
        if len(logs_df) < 100:
            logger.warning("Not enough data to train model (need at least 100 samples)")
            return False

        try:
            features = self.extract_features(logs_df)

            # Train Isolation Forest
            self.model = IsolationForest(
                contamination=0.1,  # Expect 10% anomalies
                random_state=42,
                n_estimators=100
            )

            self.model.fit(features)
            self.is_trained = True

            # Save model and encoders
            self.save_model()

            logger.info(f"Model trained successfully on {len(logs_df)} samples")
            return True

        except Exception as e:
            logger.error(f"Error training model: {e}")
            return False

    def predict(self, log_dict):
        """Predict if a single log entry is anomalous"""
        if not self.is_trained:
            logger.warning("Model not trained yet")
            return False, 0.0

        try:
            # Convert single log to DataFrame
            log_df = pd.DataFrame([log_dict])
            features = self.extract_features(log_df)

            # Predict
            prediction = self.model.predict(features)[0]
            score = self.model.decision_function(features)[0]

            is_anomaly = prediction == -1
            # Normalize score to 0-1 range (lower scores = more anomalous)
            normalized_score = (score + 0.5) / 1.0  # Rough normalization
            normalized_score = max(0, min(1, normalized_score))

            return is_anomaly, float(normalized_score)

        except Exception as e:
            logger.error(f"Error predicting anomaly: {e}")
            return False, 0.0

    def save_model(self):
        """Save the trained model and encoders"""
        try:
            with open(MODEL_PATH, 'wb') as f:
                pickle.dump(self.model, f)

            encoders = {
                'service_encoder': self.service_encoder,
                'level_encoder': self.level_encoder
            }
            with open(ENCODERS_PATH, 'wb') as f:
                pickle.dump(encoders, f)

            logger.info("Model and encoders saved successfully")
        except Exception as e:
            logger.error(f"Error saving model: {e}")

    def load_model(self):
        """Load the trained model and encoders"""
        try:
            if os.path.exists(MODEL_PATH) and os.path.exists(ENCODERS_PATH):
                with open(MODEL_PATH, 'rb') as f:
                    self.model = pickle.load(f)

                with open(ENCODERS_PATH, 'rb') as f:
                    encoders = pickle.load(f)
                    self.service_encoder = encoders['service_encoder']
                    self.level_encoder = encoders['level_encoder']

                self.is_trained = True
                logger.info("Model and encoders loaded successfully")
                return True
        except Exception as e:
            logger.error(f"Error loading model: {e}")

        return False

    def train_shared(self, logs_df):
        """
        Train only if no shared model exists yet.
        Uses a lockfile so only one consumer trains; others wait and load.
        Returns True if this consumer trained, False if it loaded instead.
        """
        import fcntl

        LOCK_PATH = "/app/models/train.lock"
        READY_PATH = "/app/models/model_ready"

        # Fast path: model already written by a previous run or another consumer
        if os.path.exists(READY_PATH):
            if not self.is_trained:
                self.load_model()
            return False

        lock_file = open(LOCK_PATH, 'w')
        try:
            # Non-blocking attempt to acquire exclusive lock
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Re-check inside the lock (another consumer may have just finished)
            if os.path.exists(READY_PATH):
                fcntl.flock(lock_file, fcntl.LOCK_UN)
                lock_file.close()
                self.load_model()
                return False

            # This consumer won the lock — train and save
            logger.info("Acquired training lock — training shared model...")
            success = self.train(logs_df)  # saves model + encoders
            if success:
                open(READY_PATH, 'w').close()  # signal others
                logger.info("Shared model written and ready.")
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()
            return success

        except BlockingIOError:
            # Another consumer holds the lock — wait for it to finish
            lock_file.close()
            logger.info("Another consumer is training — waiting for shared model...")
            self._wait_for_shared_model(READY_PATH)
            return False

    def _wait_for_shared_model(self, ready_path, timeout=120):
        """Block until the shared model is written or timeout expires."""
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            if os.path.exists(ready_path):
                self.load_model()
                logger.info("Shared model loaded successfully.")
                return
            time.sleep(2)
        logger.error("Timed out waiting for shared model. Will retry on next message.")
