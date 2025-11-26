"""
Pytest configuration and shared fixtures for integration_coworker tests.
"""
import pytest
import sqlite3
from integration_coworker.persistence import db


@pytest.fixture(scope="function", autouse=True)
def reset_db():
    """
    Reset database before each test to ensure clean state.
    
    This fixture runs automatically before every test function.
    Prevents database locking by ensuring clean setup/teardown.
    """
    # Close any lingering connections
    try:
        conn = db.get_connection()
        conn.close()
    except Exception:
        pass
    
    # Initialize schema (idempotent)
    db.init_schema()
    
    # Clear any existing test data
    try:
        db.clear_test_data()
    except sqlite3.OperationalError:
        # If DB is locked, try again after a moment
        import time
        time.sleep(0.1)
        try:
            db.clear_test_data()
        except Exception:
            pass
    
    # Run the test
    yield
    
    # Clean up after test - ensure no connections left open
    try:
        db.clear_test_data()
    except Exception:
        # If cleanup fails, it's not critical
        pass
