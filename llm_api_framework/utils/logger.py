import logging
from typing import Dict, Any
from ..core.client import APIClient

class Logger:
    def __init__(self, log_file: str = "llm_api.log"):
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def log_request(self, client: APIClient, prompt: str):
        """Log API request details"""
        self.logger.info(
            f"Request to {client.__class__.__name__} - Prompt: {prompt[:100]}..."
        )

    def log_response(self, response: Dict[str, Any]):
        """Log API response details"""
        if "error" in response:
            self.logger.error(f"API Error: {response['error']}")
        else:
            self.logger.info(
                f"API Response - Content length: {len(response.get('content', ''))}"
            )

    def log_error(self, error: Exception):
        """Log system errors"""
        self.logger.error(f"System Error: {str(error)}", exc_info=True)